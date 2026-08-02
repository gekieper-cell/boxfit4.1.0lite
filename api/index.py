import sys
import os
import csv
import io
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, Response
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Alumno, Clase, AsistenciaClase, Producto, Venta
from datetime import datetime, date, timedelta
from sqlalchemy import func, or_

app = Flask(__name__)

# Configuración de Seguridad y Base de Datos
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'boxfit_secret_key_2026')

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
    db.create_all()
    try:
        if not User.query.filter_by(username='admin').first():
            admin_user = User(
                username='admin',
                password=generate_password_hash('admin123'),
                role='admin'
            )
            db.session.add(admin_user)
            db.session.commit()
            print(">>> Usuario admin creado: admin / admin123")
    except Exception as e:
        db.session.rollback()
        print(f">>> No se pudo crear admin (probablemente ya existe): {e}")

login_manager = LoginManager(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ====================== DASHBOARD ======================

@app.route('/')
@login_required
def index():
    hoy = date.today()
    fecha_alerta = hoy + timedelta(days=7)

    total_alumnos = Alumno.query.filter_by(activo=True, estado='activo').count()
    alumnos_vencidos = Alumno.query.filter(Alumno.activo == True, Alumno.estado == 'activo', Alumno.fecha_vencimiento <= hoy).count()
    alumnos_alerta = Alumno.query.filter(Alumno.activo == True, Alumno.estado == 'activo', Alumno.fecha_vencimiento > hoy, Alumno.fecha_vencimiento <= fecha_alerta).count()
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
    }

    return render_template('dashboard.html', stats=stats)

# ====================== LOGIN / LOGOUT ======================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('index'))
        else:
            flash('Usuario o contraseña incorrectos', 'danger')

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# ====================== ALUMNOS ======================

@app.route('/alumnos')
@login_required
def alumnos():
    hoy = date.today()
    filtro = request.args.get('filtro')
    query = Alumno.query.filter_by(activo=True, estado='activo')

    if filtro == 'deudores':
        query = query.filter_by(morosidad=True)
    elif filtro == 'vencimientos':
        fecha_alerta = hoy + timedelta(days=7)
        query = query.filter(Alumno.fecha_vencimiento > hoy, Alumno.fecha_vencimiento <= fecha_alerta)

    alumnos_list = query.order_by(Alumno.nombre).all()
    return render_template('alumnos.html', alumnos=alumnos_list, filtro_actual=filtro)

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
    alumno.ultimo_pago = date.today()
    alumno.morosidad = False
    alumno.fecha_vencimiento = date.today() + timedelta(days=30)
    db.session.commit()
    flash('Pago registrado', 'success')
    return redirect(url_for('alumnos'))

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
    productos = Producto.query.all()
    return render_template('productos.html', productos=productos)

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

    return render_template('nuevo_producto.html')

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

@app.route('/usuario/reset_password/<int:id>')
@login_required
def reset_password(id):
    if current_user.role != 'admin':
        flash('No tenés permisos para esta acción', 'danger')
        return redirect(url_for('index'))

    usuario = User.query.get_or_404(id)
    usuario.password = generate_password_hash('123456')
    db.session.commit()
    flash(f'Contraseña de {usuario.username} restablecida a 123456', 'success')
    return redirect(url_for('usuarios'))

@app.route('/usuario/eliminar/<int:id>')
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
