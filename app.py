import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Alumno, Clase, AsistenciaClase, Producto, Venta
from datetime import datetime, date, timedelta
from sqlalchemy import func, or_

app = Flask(__name__)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'boxfit_secret_key_2026')

db_url = os.environ.get('DATABASE_URL')
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///gym.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

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
    
    # Estadísticas para los widgets superiores
    total_alumnos = Alumno.query.filter_by(activo=True).count()
    alumnos_morosos = Alumno.query.filter_by(morosidad=True, activo=True).count()
    
    # Próximos vencimientos (alerta: vencen en los próximos 7 días)
    fecha_alerta = hoy + timedelta(days=7)
    alumnos_vencidos = Alumno.query.filter(
        Alumno.activo == True,
        Alumno.fecha_vencimiento <= hoy
    ).count()

    alumnos_alerta = Alumno.query.filter(
        Alumno.activo == True,
        Alumno.fecha_vencimiento > hoy,
        Alumno.fecha_vencimiento <= fecha_alerta
    ).count()
    
    # Asistencias y Clases
    asistencias_hoy = AsistenciaClase.query.filter_by(fecha=hoy).count()
    dias_semana = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
    dia_actual = dias_semana[hoy.weekday()]
    clases_hoy = Clase.query.filter_by(dia=dia_actual).all()
    
    # Últimos alumnos registrados
    ultimos_alumnos = Alumno.query.order_by(Alumno.id.desc()).limit(5).all()
    productos = Producto.query.filter(Producto.stock > 0).limit(10).all()
    
    stats = {
        'total_alumnos': total_alumnos,
        'alumnos_morosos': alumnos_morosos,
        'alumnos_vencidos': alumnos_vencidos,
        'alumnos_alerta': alumnos_alerta,
        'asistencias_hoy': asistencias_hoy,
        'clases_hoy': clases_hoy,
        'ultimos_alumnos': ultimos_alumnos,
        'productos': productos
    }
    
    return render_template('dashboard.html', stats=stats, now=datetime.now())

# ====================== ALUMNOS (CON FILTROS) ======================

@app.route('/alumnos')
@login_required
def alumnos():
    filtro = request.args.get('filtro')
    query = Alumno.query.filter_by(activo=True)
    hoy = date.today()

    if filtro == 'deudores':
        query = query.filter_by(morosidad=True)
    elif filtro == 'vencimientos':
        # Muestra los que vencen en la próxima semana
        proxima_semana = hoy + timedelta(days=7)
        query = query.filter(Alumno.fecha_vencimiento <= proxima_semana)
    
    alumnos_lista = query.order_by(Alumno.nombre).all()
    return render_template('alumnos.html', alumnos=alumnos_lista)

@app.route('/alumnos/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_alumno():
    if request.method == 'POST':
        try:
            fecha_inicio = datetime.strptime(request.form['fecha_inicio'], '%Y-%m-%d').date() if request.form.get('fecha_inicio') else date.today()
            # El vencimiento por defecto es un mes después del inicio
            fecha_vencimiento = fecha_inicio + timedelta(days=30)
            
            nuevo = Alumno(
                nombre=request.form['nombre'].upper(), # Normalizamos a mayúsculas
                dni=request.form['dni'],
                telefono=request.form.get('telefono', ''),
                fecha_inicio=fecha_inicio,
                fecha_vencimiento=fecha_vencimiento,
                tipo_clase=request.form.get('tipo_clase'),
                valor_cuota=float(request.form.get('valor_cuota', 15000)),
                clases_totales=int(request.form.get('clases_totales', 0)),
                clases_restantes=int(request.form.get('clases_totales', 0)),
                activo=True,
                morosidad=False
            )
            db.session.add(nuevo)
            db.session.commit()
            flash(f'Alumno {nuevo.nombre} registrado con éxito', 'success')
            return redirect(url_for('alumnos'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error al registrar: {str(e)}', 'error')
    
    return render_template('nuevo_alumno.html')

@app.route('/alumnos/registrar_pago/<int:id>', methods=['POST'])
@login_required
def registrar_pago(id):
    alumno = Alumno.query.get_or_404(id)
    try:
        # Al pagar, renovamos la fecha de vencimiento a 30 días desde hoy
        alumno.ultimo_pago = date.today()
        alumno.fecha_vencimiento = date.today() + timedelta(days=30)
        alumno.morosidad = False
        
        # Si tiene plan de clases, reseteamos el contador
        if alumno.clases_totales > 0:
            alumno.clases_restantes = alumno.clases_totales
            
        db.session.commit()
        flash(f'Pago procesado. Nueva fecha de vencimiento: {alumno.fecha_vencimiento.strftime("%d/%m/%Y")}', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al procesar pago: {str(e)}', 'error')
        
    return redirect(url_for('alumnos'))

# ====================== ASISTENCIA ======================

@app.route('/asistencia/registrar', methods=['POST'])
@login_required
def registrar_asistencia():
    alumno_id = request.form.get('alumno_id')
    clase_id = request.form.get('clase_id')
    hoy = date.today()
    
    alumno = Alumno.query.get(alumno_id)
    if not alumno or not alumno.activo:
        flash('Alumno no válido o inactivo', 'error')
        return redirect(url_for('clases'))
    
    # Bloqueo por morosidad (Opcional, puedes quitarlo si permites entrenar debiendo)
    if alumno.morosidad:
        flash(f'⚠️ {alumno.nombre} tiene cuotas pendientes.', 'error')
    
    # Verificar duplicados el mismo día
    ya_asistio = AsistenciaClase.query.filter_by(alumno_id=alumno_id, fecha=hoy).first()
    if ya_asistio:
        flash(f'{alumno.nombre} ya registró su ingreso hoy.', 'warning')
        return redirect(url_for('clases'))

    nueva_asistencia = AsistenciaClase(alumno_id=alumno_id, clase_id=clase_id, fecha=hoy)
    alumno.asistencia += 1
    
    if alumno.clases_restantes > 0:
        alumno.clases_restantes -= 1
        
    db.session.add(nueva_asistencia)
    db.session.commit()
    flash(f'✅ Entrada registrada: {alumno.nombre}', 'success')
    
    return redirect(url_for('clases'))

# ====================== LOGIN / LOGOUT ======================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and check_password_hash(user.password, request.form.get('password')):
            login_user(user)
            return redirect(url_for('index'))
        flash('Credenciales inválidas', 'error')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# ... (Mantener las rutas de Ventas, Usuarios e Inicialización igual que en tu código original)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)