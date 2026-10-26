import sys
import os
import csv
import io
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, Response
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Alumno, Clase, AsistenciaClase, Producto, Venta, Pago
from datetime import datetime, date, timedelta
from sqlalchemy import func, or_, inspect, text
from flask import session
import secrets
import pyotp
import qrcode
import qrcode.image.svg

app = Flask(__name__)

# Configuración de Seguridad y Base de Datos
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'boxfit_secret_key_2026')
if 'SECRET_KEY' not in os.environ:
    print(">>> AVISO: SECRET_KEY no configurada; las sesiones usan una clave conocida")

# Cookies de sesión: solo por HTTPS en Vercel, no accesibles desde JS, y vencen a las 8 horas
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = bool(os.environ.get('VERCEL'))
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)

MAX_INTENTOS = 5
MINUTOS_BLOQUEO = 15

# Configuración para PostgreSQL (Supabase) o SQLite local
db_url = os.environ.get('DATABASE_URL')
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///gym.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

DIAS_SEMANA = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']

# ====================== INICIALIZACIÓN DE BASE DE DATOS ======================
with app.app_context():
    try:
        db.create_all()
    except Exception as e:
        db.session.rollback()
        print(f">>> ERROR creando tablas: {e}")

    # create_all no modifica tablas existentes: agregamos las columnas nuevas
    # del modelo que falten en la base (por ejemplo, en Supabase).
    try:
        inspector = inspect(db.engine)
        for tabla in db.metadata.sorted_tables:
            if not inspector.has_table(tabla.name):
                continue
            existentes = {c['name'] for c in inspector.get_columns(tabla.name)}
            for col in tabla.columns:
                if col.name in existentes:
                    continue
                tipo = col.type.compile(dialect=db.engine.dialect)
                with db.engine.begin() as conn:
                    conn.execute(text(f'ALTER TABLE {tabla.name} ADD COLUMN {col.name} {tipo}'))
                print(f">>> Columna agregada: {tabla.name}.{col.name}")
    except Exception as e:
        print(f">>> ERROR agregando columnas: {e}")
    try:
        # Solo se crea un admin inicial si la base no tiene ningún usuario
        if User.query.count() == 0:
            clave_inicial = os.environ.get('ADMIN_PASSWORD') or secrets.token_urlsafe(9)
            admin_user = User(
                username='admin',
                password=generate_password_hash(clave_inicial),
                role='admin'
            )
            db.session.add(admin_user)
            db.session.commit()
            print(f">>> Usuario admin creado: admin / {clave_inicial}")
    except Exception as e:
        db.session.rollback()
        print(f">>> No se pudo crear admin (probablemente ya existe): {e}")

login_manager = LoginManager(app)
login_manager.login_view = 'login'

@login_manager.unauthorized_handler
def no_autorizado():
    # El ingreso es oculto: quien no está logueado vuelve a la página pública
    return redirect(url_for('index'))

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ====================== DASHBOARD ======================

@app.route('/')
def index():
    # Visitantes: página pública del club. Staff logueado: dashboard.
    if not current_user.is_authenticated:
        return landing()
    return dashboard()

def landing():
    # Si la base falla, la página pública igual tiene que mostrarse (sin horarios)
    try:
        clases = Clase.query.all()
    except Exception:
        app.logger.exception('No se pudieron leer las clases para la landing')
        db.session.rollback()
        clases = []
    orden = {d: i for i, d in enumerate(DIAS_SEMANA)}
    por_dia = {}
    for c in sorted(clases, key=lambda c: (orden.get(c.dia, 99), c.hora or '')):
        por_dia.setdefault(c.dia or 'A confirmar', []).append(c)
    whatsapp = ''.join(ch for ch in os.environ.get('WHATSAPP', '') if ch.isdigit())
    return render_template('landing.html', horarios=list(por_dia.items()), whatsapp=whatsapp)

def dashboard():
    hoy = date.today()
    fecha_alerta = hoy + timedelta(days=7)

    total_alumnos = Alumno.query.filter_by(activo=True, estado='activo').count()
    alumnos_vencidos = Alumno.query.filter(Alumno.activo == True, Alumno.estado == 'activo', Alumno.fecha_vencimiento < hoy).count()
    alumnos_alerta = Alumno.query.filter(Alumno.activo == True, Alumno.estado == 'activo', Alumno.fecha_vencimiento >= hoy, Alumno.fecha_vencimiento <= fecha_alerta).count()
    alumnos_pausados = Alumno.query.filter_by(activo=True, estado='pausado').count()

    # Cobranza del mes
    inicio_mes = hoy.replace(day=1)
    cobrado_mes = db.session.query(func.coalesce(func.sum(Pago.monto), 0)).filter(Pago.fecha >= inicio_mes).scalar()
    ventas_mes = db.session.query(func.coalesce(func.sum(Venta.monto), 0)).filter(Venta.fecha >= datetime.combine(inicio_mes, datetime.min.time())).scalar()
    esperado_mes = db.session.query(func.coalesce(func.sum(Alumno.valor_cuota), 0)).filter(Alumno.activo == True, Alumno.estado == 'activo').scalar()
    al_dia = total_alumnos - alumnos_vencidos
    pct_al_dia = round(al_dia * 100 / total_alumnos) if total_alumnos else 0

    # Asistencia: % de alumnos activos que vinieron al menos una vez en los últimos N días
    def pct_vinieron(dias):
        desde = hoy - timedelta(days=dias - 1)
        vinieron = db.session.query(func.count(func.distinct(AsistenciaClase.alumno_id))) \
            .join(Alumno, Alumno.id == AsistenciaClase.alumno_id) \
            .filter(AsistenciaClase.fecha >= desde, Alumno.activo == True, Alumno.estado == 'activo').scalar()
        return round(vinieron * 100 / total_alumnos) if total_alumnos else 0
    asist_30 = AsistenciaClase.query.filter(AsistenciaClase.fecha >= hoy - timedelta(days=29)).count()
    promedio_semanal = round(asist_30 / total_alumnos / (30 / 7), 1) if total_alumnos else 0
    asistencias_hoy = AsistenciaClase.query.filter_by(fecha=hoy).count()
    clases_hoy = Clase.query.filter_by(dia=DIAS_SEMANA[hoy.weekday()]).all()
    ultimos_alumnos = Alumno.query.filter_by(activo=True, estado='activo').order_by(Alumno.id.desc()).limit(10).all()
    productos = Producto.query.filter(Producto.stock > 0).all()

    stats = {
        'total_alumnos': total_alumnos,
        'alumnos_vencidos': alumnos_vencidos,
        'alumnos_alerta': alumnos_alerta,
        'asistencias_hoy': asistencias_hoy,
        'clases_hoy': clases_hoy,
        'ultimos_alumnos': ultimos_alumnos,
        'productos': productos,
        'alumnos_pausados': alumnos_pausados,
        'cobrado_mes': cobrado_mes,
        'ventas_mes': ventas_mes,
        'esperado_mes': esperado_mes,
        'al_dia': al_dia,
        'pct_al_dia': pct_al_dia,
        'pct_asist_7': pct_vinieron(7),
        'pct_asist_30': pct_vinieron(30),
        'promedio_semanal': promedio_semanal,
    }

    return render_template('dashboard.html', stats=stats, hoy=hoy)

# ====================== LOGIN / LOGOUT ======================

@app.route('/admin', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()

        if user and user.bloqueado_hasta and user.bloqueado_hasta > datetime.now():
            flash(f'Demasiados intentos fallidos. Probá de nuevo en {MINUTOS_BLOQUEO} minutos.', 'error')
        elif user and check_password_hash(user.password, password):
            if user.totp_secret:
                # Contraseña correcta: falta el código de la app autenticadora
                session['2fa_uid'] = user.id
                session['2fa_desde'] = datetime.now().timestamp()
                return redirect(url_for('verificar_2fa'))
            return completar_login(user)
        else:
            registrar_fallo(user)
            flash('Usuario o contraseña incorrectos', 'error')

    return render_template('login.html', paso='clave')

@app.route('/admin/codigo', methods=['GET', 'POST'])
def verificar_2fa():
    uid = session.get('2fa_uid')
    # El paso del código vence a los 5 minutos de haber puesto la contraseña
    if not uid or datetime.now().timestamp() - session.get('2fa_desde', 0) > 300:
        session.pop('2fa_uid', None)
        return redirect(url_for('login'))
    user = User.query.get(uid)
    if not user or not user.totp_secret:
        session.pop('2fa_uid', None)
        return redirect(url_for('login'))

    if request.method == 'POST':
        if user.bloqueado_hasta and user.bloqueado_hasta > datetime.now():
            session.pop('2fa_uid', None)
            flash(f'Demasiados intentos fallidos. Probá de nuevo en {MINUTOS_BLOQUEO} minutos.', 'error')
            return redirect(url_for('login'))
        codigo = request.form.get('codigo', '').replace(' ', '')
        if pyotp.TOTP(user.totp_secret).verify(codigo, valid_window=1):
            session.pop('2fa_uid', None)
            return completar_login(user)
        registrar_fallo(user)
        flash('Código incorrecto. Usá el código de 6 dígitos que muestra la app.', 'error')

    return render_template('login.html', paso='codigo')

def registrar_fallo(user):
    if not user:
        return
    user.intentos_fallidos = (user.intentos_fallidos or 0) + 1
    if user.intentos_fallidos >= MAX_INTENTOS:
        user.bloqueado_hasta = datetime.now() + timedelta(minutes=MINUTOS_BLOQUEO)
        user.intentos_fallidos = 0
    db.session.commit()

def completar_login(user):
    user.intentos_fallidos = 0
    user.bloqueado_hasta = None
    db.session.commit()
    session.permanent = True
    login_user(user)
    if user.role == 'admin' and not user.totp_secret:
        flash('Activá la verificación en dos pasos desde Seguridad para proteger la cuenta.', 'warning')
    return redirect(url_for('index'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('index'))

# ====================== SEGURIDAD (2FA Y CONTRASEÑA) ======================

def qr_svg(uri):
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8)
    return img.to_string(encoding='unicode')

@app.route('/seguridad', methods=['GET', 'POST'])
@login_required
def seguridad():
    user = current_user
    if request.method == 'POST':
        accion = request.form.get('accion')

        if accion == 'clave':
            actual = request.form.get('actual', '')
            nueva = request.form.get('nueva', '')
            if not check_password_hash(user.password, actual):
                flash('La contraseña actual no es correcta', 'danger')
            elif len(nueva) < 10:
                flash('La contraseña nueva tiene que tener al menos 10 caracteres', 'danger')
            elif nueva != request.form.get('repetir', ''):
                flash('Las contraseñas nuevas no coinciden', 'danger')
            else:
                user.password = generate_password_hash(nueva)
                db.session.commit()
                flash('Contraseña cambiada', 'success')

        elif accion == 'activar_2fa':
            secreto = session.get('2fa_nuevo')
            codigo = request.form.get('codigo', '').replace(' ', '')
            if secreto and pyotp.TOTP(secreto).verify(codigo, valid_window=1):
                user.totp_secret = secreto
                db.session.commit()
                session.pop('2fa_nuevo', None)
                flash('Verificación en dos pasos activada', 'success')
            else:
                flash('Código incorrecto. Escaneá el QR y usá el código que muestra la app.', 'danger')

        elif accion == 'desactivar_2fa':
            codigo = request.form.get('codigo', '').replace(' ', '')
            if user.totp_secret and pyotp.TOTP(user.totp_secret).verify(codigo, valid_window=1):
                user.totp_secret = None
                db.session.commit()
                flash('Verificación en dos pasos desactivada', 'success')
            else:
                flash('Código incorrecto. La verificación en dos pasos sigue activa.', 'danger')

        return redirect(url_for('seguridad'))

    qr = secreto = None
    if not user.totp_secret:
        # Secreto nuevo guardado en sesión hasta que se confirme con un código
        secreto = session.get('2fa_nuevo') or pyotp.random_base32()
        session['2fa_nuevo'] = secreto
        uri = pyotp.TOTP(secreto).provisioning_uri(name=user.username, issuer_name='Boxing Club Profe Medina')
        qr = qr_svg(uri)
    return render_template('seguridad.html', qr=qr, secreto=secreto)

@app.errorhandler(Exception)
def error_interno(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    import traceback
    detalle = traceback.format_exc()
    app.logger.error(detalle)
    try:
        db.session.rollback()
    except Exception:
        pass
    # El detalle queda solo en los logs de Vercel, nunca en la respuesta
    if 'OperationalError' in detalle:
        return Response('No se pudo conectar con la base de datos. Revisá DATABASE_URL en Vercel.', status=500, mimetype='text/plain; charset=utf-8')
    return Response('Error interno', status=500, mimetype='text/plain')

@app.after_request
def cabeceras_seguridad(resp):
    resp.headers['X-Frame-Options'] = 'DENY'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['Referrer-Policy'] = 'same-origin'
    if request.path != '/' and not request.path.startswith('/static'):
        # Las páginas del panel no se guardan en caché ni se indexan
        resp.headers['Cache-Control'] = 'no-store'
        resp.headers['X-Robots-Tag'] = 'noindex, nofollow'
    return resp

# ====================== ALUMNOS ======================

@app.route('/alumnos')
@login_required
def alumnos():
    hoy = date.today()
    filtro = request.args.get('filtro')
    query = Alumno.query.filter_by(activo=True, estado='activo')

    q = (request.args.get('q') or '').strip()
    if filtro == 'deudores':
        query = query.filter(Alumno.fecha_vencimiento < hoy)
    elif filtro == 'vencimientos':
        fecha_alerta = hoy + timedelta(days=7)
        query = query.filter(Alumno.fecha_vencimiento >= hoy, Alumno.fecha_vencimiento <= fecha_alerta)
    elif filtro == 'pausados':
        query = Alumno.query.filter_by(activo=True, estado='pausado')
    elif filtro == 'bajas':
        query = Alumno.query.filter_by(activo=False)
    if q:
        query = query.filter(or_(Alumno.nombre.ilike(f'%{q}%'), Alumno.dni.ilike(f'%{q}%')))

    alumnos_list = query.order_by(Alumno.nombre).all()
    return render_template('alumnos.html', alumnos=alumnos_list, filtro_actual=filtro, q=q,
                           desde_30=hoy - timedelta(days=29))

@app.route('/alumno/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_alumno():
    if request.method == 'POST':
        fecha_inicio_str = request.form.get('fecha_inicio')
        ultimo_pago_str = request.form.get('ultimo_pago')
        fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date() if fecha_inicio_str else date.today()
        ultimo_pago = datetime.strptime(ultimo_pago_str, '%Y-%m-%d').date() if ultimo_pago_str else None
        clases_totales = request.form.get('clases_totales') or 0
        valor_cuota = request.form.get('valor_cuota') or 15000

        nuevo = Alumno(
            nombre=request.form.get('nombre'),
            dni=request.form.get('dni'),
            telefono=request.form.get('telefono'),
            contacto_emergencia=request.form.get('contacto_emergencia'),
            telefono_emergencia=request.form.get('telefono_emergencia'),
            fecha_inicio=fecha_inicio,
            fecha_vencimiento=fecha_inicio + timedelta(days=30),
            ultimo_pago=ultimo_pago,
            tipo_clase=request.form.get('tipo_clase'),
            valor_cuota=float(valor_cuota),
            forma_pago=request.form.get('forma_pago'),
            clases_totales=int(clases_totales),
            clases_restantes=int(clases_totales),
            activo=True,
            estado='activo',
            morosidad=False
        )
        try:
            db.session.add(nuevo)
            db.session.commit()
            flash('Alumno agregado exitosamente', 'success')
        except Exception:
            db.session.rollback()
            flash('No se pudo guardar: revisá que el DNI no esté repetido', 'danger')

        return redirect(url_for('alumnos'))

    return render_template('nuevo_alumno.html')

@app.route('/alumno/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_alumno(id):
    alumno = Alumno.query.get_or_404(id)

    if request.method == 'POST':
        alumno.nombre = request.form.get('nombre')
        alumno.dni = request.form.get('dni')
        alumno.telefono = request.form.get('telefono')
        alumno.contacto_emergencia = request.form.get('contacto_emergencia')
        alumno.telefono_emergencia = request.form.get('telefono_emergencia')
        alumno.tipo_clase = request.form.get('tipo_clase')
        valor_cuota = request.form.get('valor_cuota')
        if valor_cuota:
            alumno.valor_cuota = float(valor_cuota)
        alumno.forma_pago = request.form.get('forma_pago')
        clases_totales = request.form.get('clases_totales')
        if clases_totales:
            alumno.clases_totales = int(clases_totales)
        alumno.notas = request.form.get('notas')

        if request.form.get('dar_baja'):
            alumno.activo = False
            alumno.estado = 'inactivo'
            alumno.fecha_baja = date.today()
            alumno.motivo_baja = request.form.get('motivo_baja')

        try:
            db.session.commit()
            flash('Alumno actualizado', 'success')
        except Exception:
            db.session.rollback()
            flash('No se pudo actualizar: revisá que el DNI no esté repetido', 'danger')

        return redirect(url_for('alumnos'))

    return render_template('editar_alumno.html', alumno=alumno)

@app.route('/alumno/eliminar/<int:id>', methods=['POST'])
@login_required
def eliminar_alumno(id):
    alumno = Alumno.query.get_or_404(id)
    alumno.activo = False
    alumno.estado = 'inactivo'
    alumno.fecha_baja = date.today()
    db.session.commit()
    flash('Alumno dado de baja', 'success')
    return redirect(url_for('alumnos'))

@app.route('/alumno/reactivar/<int:id>', methods=['POST'])
@login_required
def reactivar_alumno(id):
    alumno = Alumno.query.get_or_404(id)
    alumno.activo = True
    alumno.estado = 'activo'
    alumno.fecha_pausa = None
    alumno.motivo_pausa = None
    db.session.commit()
    flash('Alumno reactivado', 'success')
    return redirect(url_for('alumnos'))

@app.route('/alumno/pago/<int:id>', methods=['POST'])
@login_required
def registrar_pago(id):
    alumno = Alumno.query.get_or_404(id)
    hoy = date.today()
    try:
        monto = float(request.form.get('monto') or alumno.valor_cuota or 0)
        meses = max(1, int(request.form.get('meses') or 1))
    except ValueError:
        flash('Monto inválido', 'danger')
        return redirect(request.referrer or url_for('alumnos'))

    # Si paga antes de vencer, se extiende desde el vencimiento actual (no pierde días)
    base = alumno.fecha_vencimiento if alumno.fecha_vencimiento and alumno.fecha_vencimiento > hoy else hoy
    alumno.fecha_vencimiento = base + timedelta(days=30 * meses)
    alumno.ultimo_pago = hoy
    alumno.morosidad = False
    db.session.add(Pago(alumno_id=alumno.id, monto=monto, metodo=request.form.get('metodo') or 'Efectivo',
                        fecha=hoy, periodo_hasta=alumno.fecha_vencimiento, usuario_id=current_user.id))
    db.session.commit()
    flash(f'Pago de ${monto:,.0f} registrado. Vence el {alumno.fecha_vencimiento.strftime("%d/%m/%Y")}', 'success')
    return redirect(request.referrer or url_for('alumnos'))

@app.route('/pago/anular/<int:id>', methods=['POST'])
@login_required
def anular_pago(id):
    if current_user.role != 'admin':
        flash('Solo un administrador puede anular pagos', 'danger')
        return redirect(url_for('pagos'))
    pago = Pago.query.get_or_404(id)
    alumno = pago.alumno_rel
    db.session.delete(pago)
    # Recalcular vencimiento según el último pago que queda
    ultimo = Pago.query.filter(Pago.alumno_id == alumno.id, Pago.id != id).order_by(Pago.fecha.desc(), Pago.id.desc()).first()
    alumno.fecha_vencimiento = ultimo.periodo_hasta if ultimo and ultimo.periodo_hasta else (alumno.fecha_inicio or date.today())
    alumno.ultimo_pago = ultimo.fecha if ultimo else None
    db.session.commit()
    flash('Pago anulado', 'success')
    return redirect(request.referrer or url_for('pagos'))

# ====================== COBRANZAS ======================

def _rango_mes(mes_str):
    try:
        inicio = datetime.strptime(mes_str, '%Y-%m').date()
    except (TypeError, ValueError):
        inicio = date.today().replace(day=1)
    fin = (inicio.replace(day=28) + timedelta(days=4)).replace(day=1)
    return inicio, fin

@app.route('/pagos')
@login_required
def pagos():
    inicio, fin = _rango_mes(request.args.get('mes'))
    lista = Pago.query.filter(Pago.fecha >= inicio, Pago.fecha < fin).order_by(Pago.fecha.desc(), Pago.id.desc()).all()
    por_metodo = {}
    for p in lista:
        por_metodo[p.metodo] = por_metodo.get(p.metodo, 0) + p.monto
    deudores = Alumno.query.filter(Alumno.activo == True, Alumno.estado == 'activo', Alumno.fecha_vencimiento < date.today()) \
        .order_by(Alumno.fecha_vencimiento).all()
    return render_template('pagos.html', pagos=lista, total=sum(por_metodo.values()), por_metodo=por_metodo,
                           mes=inicio.strftime('%Y-%m'), deudores=deudores, hoy=date.today())

@app.route('/pagos/exportar')
@login_required
def exportar_pagos():
    inicio, fin = _rango_mes(request.args.get('mes'))
    lista = Pago.query.filter(Pago.fecha >= inicio, Pago.fecha < fin).order_by(Pago.fecha).all()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['fecha', 'alumno', 'dni', 'monto', 'metodo', 'vence'])
    for p in lista:
        writer.writerow([p.fecha.strftime('%d/%m/%Y'), p.alumno_rel.nombre, p.alumno_rel.dni, f'{p.monto:.0f}', p.metodo,
                         p.periodo_hasta.strftime('%d/%m/%Y') if p.periodo_hasta else ''])
    return Response('\ufeff' + output.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename=cobranzas_{inicio.strftime("%Y-%m")}.csv'})

# Ojo: esta ruta usa una URL fija (no url_for) porque asi la llama el JS de alumnos.html
@app.route('/alumnos/pausar/<int:id>', methods=['POST'])
@login_required
def pausar_alumno(id):
    alumno = Alumno.query.get_or_404(id)
    motivo = request.form.get('motivo_pausa')
    motivo_otro = request.form.get('motivo_otro')
    alumno.estado = 'pausado'
    alumno.fecha_pausa = date.today()
    alumno.motivo_pausa = motivo_otro if (motivo == 'Otro' and motivo_otro) else motivo
    db.session.commit()
    flash('Alumno pausado', 'success')
    return redirect(url_for('alumnos'))

@app.route('/alumnos/plantilla')
@login_required
def descargar_plantilla_alumnos():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['nombre', 'dni', 'telefono', 'tipo_clase', 'valor_cuota', 'fecha_inicio'])
    writer.writerow(['Juan Perez', '30111222', '5491122334455', 'Libre', '15000', '2026-01-15'])
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=plantilla_alumnos.csv'}
    )

@app.route('/alumnos/importar', methods=['POST'])
@login_required
def importar_alumnos_excel():
    archivo = request.files.get('archivo')
    if not archivo:
        flash('No se seleccionó ningún archivo', 'danger')
        return redirect(url_for('alumnos'))

    try:
        import openpyxl
    except ImportError:
        flash('Falta la librería openpyxl en el servidor (agregala a requirements.txt)', 'danger')
        return redirect(url_for('alumnos'))

    try:
        wb = openpyxl.load_workbook(archivo, data_only=True)
        hoja = wb.active
        creados = 0
        for fila in hoja.iter_rows(min_row=2, values_only=True):
            if not fila or not fila[0]:
                continue
            nombre, dni, telefono, tipo_clase, valor_cuota, fecha_inicio_raw = (list(fila) + [None] * 6)[:6]
            if not dni or Alumno.query.filter_by(dni=str(dni)).first():
                continue
            fecha_inicio = fecha_inicio_raw if isinstance(fecha_inicio_raw, date) else date.today()
            nuevo = Alumno(
                nombre=nombre,
                dni=str(dni),
                telefono=str(telefono) if telefono else None,
                tipo_clase=tipo_clase,
                valor_cuota=float(valor_cuota) if valor_cuota else 15000.0,
                fecha_inicio=fecha_inicio,
                fecha_vencimiento=fecha_inicio + timedelta(days=30),
                activo=True,
                estado='activo',
                morosidad=False
            )
            db.session.add(nuevo)
            creados += 1
        db.session.commit()
        flash(f'Se importaron {creados} alumnos correctamente', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al importar el archivo: {e}', 'danger')

    return redirect(url_for('alumnos'))

# ====================== CLASES / ASISTENCIA ======================

@app.route('/clases')
@login_required
def clases():
    todas_clases = Clase.query.order_by(Clase.dia, Clase.hora).all()
    alumnos_activos = Alumno.query.filter_by(activo=True, estado='activo').order_by(Alumno.nombre).all()
    return render_template('clases.html', clases=todas_clases, alumnos_activos=alumnos_activos)

@app.route('/clase/nueva', methods=['POST'])
@login_required
def nueva_clase():
    capacidad = request.form.get('capacidad') or 20
    clase = Clase(
        nombre=request.form.get('nombre'),
        dia=request.form.get('dia'),
        hora=request.form.get('hora'),
        capacidad=int(capacidad)
    )
    db.session.add(clase)
    db.session.commit()
    flash('Clase creada', 'success')
    return redirect(url_for('clases'))

@app.route('/clase/eliminar/<int:id>', methods=['POST'])
@login_required
def eliminar_clase(id):
    clase = Clase.query.get_or_404(id)
    db.session.delete(clase)
    db.session.commit()
    flash('Clase eliminada', 'success')
    return redirect(url_for('clases'))

@app.route('/asistencia/registrar', methods=['POST'])
@login_required
def registrar_asistencia():
    alumno_id = request.form.get('alumno_id')
    clase_id = request.form.get('clase_id')

    if alumno_id and clase_id:
        ya_existe = AsistenciaClase.query.filter_by(alumno_id=alumno_id, clase_id=clase_id, fecha=date.today()).first()
        if ya_existe:
            flash('El alumno ya estaba marcado presente hoy en esta clase', 'info')
        else:
            db.session.add(AsistenciaClase(alumno_id=alumno_id, clase_id=clase_id, fecha=date.today()))
            alumno = Alumno.query.get(alumno_id)
            if alumno:
                alumno.asistencia = (alumno.asistencia or 0) + 1
            db.session.commit()
            flash('Asistencia registrada', 'success')

    return redirect(url_for('clases'))

# ====================== PRODUCTOS / VENTAS ======================

@app.route('/productos')
@login_required
def listar_productos():
    return redirect(url_for('listar_ventas'))

@app.route('/producto/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_producto():
    if request.method == 'POST':
        producto = Producto(
            nombre=request.form.get('nombre'),
            precio=float(request.form.get('precio', 0)),
            stock=int(request.form.get('stock', 0))
        )
        db.session.add(producto)
        db.session.commit()
        flash('Producto agregado', 'success')
        return redirect(url_for('listar_ventas'))

    return redirect(url_for('listar_ventas'))

@app.route('/ventas')
@login_required
def listar_ventas():
    if current_user.role != 'admin':
        flash('No tenés permisos para acceder a esta sección', 'danger')
        return redirect(url_for('index'))

    ventas = Venta.query.order_by(Venta.fecha.desc()).all()
    productos = Producto.query.filter(Producto.stock > 0).all()
    return render_template('ventas.html', ventas=ventas, productos=productos)

@app.route('/venta/rapida', methods=['POST'])
@login_required
def venta_rapida():
    producto_id = request.form.get('producto_id')
    cantidad = int(request.form.get('cantidad', 1))

    producto = Producto.query.get(producto_id) if producto_id else None
    if producto and producto.stock >= cantidad:
        venta = Venta(
            producto_id=producto.id,
            producto_nombre=producto.nombre,
            cantidad=cantidad,
            monto=producto.precio * cantidad,
            fecha=datetime.now(),
            usuario_id=current_user.id
        )
        producto.stock -= cantidad
        db.session.add(venta)
        db.session.commit()
        flash('Venta registrada', 'success')
    else:
        flash('Stock insuficiente o producto inválido', 'danger')

    return redirect(request.referrer or url_for('index'))

# ====================== USUARIOS (SOLO ADMIN) ======================

@app.route('/usuarios')
@login_required
def usuarios():
    if current_user.role != 'admin':
        flash('No tenés permisos para acceder a esta sección', 'danger')
        return redirect(url_for('index'))

    todos_usuarios = User.query.order_by(User.username).all()
    return render_template('usuarios.html', usuarios=todos_usuarios)

@app.route('/usuario/nuevo', methods=['POST'])
@login_required
def nuevo_usuario():
    if current_user.role != 'admin':
        flash('No tenés permisos para esta acción', 'danger')
        return redirect(url_for('index'))

    username = request.form.get('username')
    password = request.form.get('password')
    role = request.form.get('role', 'operador')

    if User.query.filter_by(username=username).first():
        flash('Ese nombre de usuario ya existe', 'danger')
    else:
        db.session.add(User(username=username, password=generate_password_hash(password), role=role))
        db.session.commit()
        flash('Usuario creado', 'success')

    return redirect(url_for('usuarios'))

@app.route('/usuario/reset_password/<int:id>', methods=['POST'])
@login_required
def reset_password(id):
    if current_user.role != 'admin':
        flash('No tenés permisos para esta acción', 'danger')
        return redirect(url_for('index'))

    usuario = User.query.get_or_404(id)
    temporal = secrets.token_urlsafe(9)
    usuario.password = generate_password_hash(temporal)
    # Si perdió el celular, el reset también le quita la verificación en dos pasos
    usuario.totp_secret = None
    usuario.intentos_fallidos = 0
    usuario.bloqueado_hasta = None
    db.session.commit()
    flash(f'Contraseña temporal de {usuario.username}: {temporal} (sin verificación en dos pasos hasta que la vuelva a activar)', 'fijo')
    return redirect(url_for('usuarios'))

@app.route('/usuario/eliminar/<int:id>', methods=['POST'])
@login_required
def eliminar_usuario(id):
    if current_user.role != 'admin':
        flash('No tenés permisos para esta acción', 'danger')
        return redirect(url_for('index'))

    if id == current_user.id:
        flash('No podés eliminarte a vos mismo', 'danger')
        return redirect(url_for('usuarios'))

    usuario = User.query.get_or_404(id)
    db.session.delete(usuario)
    db.session.commit()
    flash('Usuario eliminado', 'success')
    return redirect(url_for('usuarios'))

# ====================== MANEJADOR PARA VERCEL ======================
application = app

# ====================== EJECUCIÓN LOCAL ======================
if __name__ == '__main__':
    app.run(debug=True)
