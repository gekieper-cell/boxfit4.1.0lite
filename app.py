import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Alumno, Clase, AsistenciaClase, Producto, Venta
from datetime import datetime, date, timedelta
from sqlalchemy import func, or_, text

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

def init_db():
    with app.app_context():
        db.create_all()
        # Migración manual para columnas faltantes en entornos existentes
        try:
            db.session.execute(text('ALTER TABLE alumnos ADD COLUMN fecha_vencimiento DATE'))
            db.session.commit()
        except Exception:
            db.session.rollback()

        try:
            db.session.execute(text('ALTER TABLE alumnos ADD COLUMN ultimo_pago DATE'))
            db.session.commit()
        except Exception:
            db.session.rollback()

# ====================== DASHBOARD ======================

@app.route('/')
@login_required
def index():
    hoy = date.today()
    total_alumnos = Alumno.query.filter_by(activo=True).count()
    alumnos_morosos = Alumno.query.filter_by(morosidad=True, activo=True).count()
    
    fecha_alerta = hoy + timedelta(days=7)
    alumnos_vencidos = Alumno.query.filter(Alumno.activo == True, Alumno.fecha_vencimiento <= hoy).count()
    alumnos_alerta = Alumno.query.filter(Alumno.activo == True, Alumno.fecha_vencimiento > hoy, Alumno.fecha_vencimiento <= fecha_alerta).count()
    
    asistencias_hoy = AsistenciaClase.query.filter_by(fecha=hoy).count()
    dias_semana = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
    clases_hoy = Clase.query.filter_by(dia=dias_semana[hoy.weekday()]).all()
    
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

# ====================== ALUMNOS ======================

@app.route('/alumnos')
@login_required
def alumnos():
    filtro = request.args.get('filtro')
    query = Alumno.query.filter_by(activo=True)
    if filtro == 'deudores':
        query = query.filter_by(morosidad=True)
    elif filtro == 'vencimientos':
        proxima_semana = date.today() + timedelta(days=7)
        query = query.filter(Alumno.fecha_vencimiento <= proxima_semana)
    
    alumnos_lista = query.order_by(Alumno.nombre).all()
    return render_template('alumnos.html', alumnos=alumnos_lista)

@app.route('/alumnos/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_alumno():
    if request.method == 'POST':
        try:
            f_inicio_str = request.form.get('fecha_inicio')
            fecha_inicio = datetime.strptime(f_inicio_str, '%Y-%m-%d').date() if f_inicio_str else date.today()
            f_pago_str = request.form.get('ultimo_pago')
            ultimo_pago = datetime.strptime(f_pago_str, '%Y-%m-%d').date() if f_pago_str else fecha_inicio
            
            nuevo = Alumno(
                nombre=request.form['nombre'].upper(),
                dni=request.form['dni'],
                telefono=request.form.get('telefono', ''),
                fecha_inicio=fecha_inicio,
                ultimo_pago=ultimo_pago,
                fecha_vencimiento=ultimo_pago + timedelta(days=30),
                tipo_clase=request.form.get('tipo_clase'),
                valor_cuota=float(request.form.get('valor_cuota', 15000)),
                forma_pago=request.form.get('forma_pago'),
                clases_totales=int(request.form.get('clases_totales', 0)),
                clases_restantes=int(request.form.get('clases_totales', 0)),
                activo=True
            )
            db.session.add(nuevo)
            db.session.commit()
            flash(f'Alumno {nuevo.nombre} creado', 'success')
            return redirect(url_for('alumnos'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {str(e)}', 'error')
    return render_template('nuevo_alumno.html')

# ====================== CLASES ======================

@app.route('/clases')
@login_required
def clases():
    todas_clases = Clase.query.all()
    alumnos_activos = Alumno.query.filter_by(activo=True).order_by(Alumno.nombre).all()
    return render_template('clases.html', clases=todas_clases, alumnos=alumnos_activos)

@app.route('/clases/nueva', methods=['POST'])
@login_required
def nueva_clase():
    try:
        nueva = Clase(
            nombre=request.form['nombre'],
            dia=request.form['dia'],
            hora=request.form['hora'],
            capacidad=int(request.form.get('capacidad', 20))
        )
        db.session.add(nueva)
        db.session.commit()
        flash('Clase creada correctamente', 'success')
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
    return redirect(url_for('clases'))

@app.route('/clases/asistencia', methods=['POST'])
@login_required
def registrar_asistencia():
    alumno_id = request.form.get('alumno_id')
    clase_id = request.form.get('clase_id')
    hoy = date.today()
    
    alumno = Alumno.query.get(alumno_id)
    ya_asistio = AsistenciaClase.query.filter_by(alumno_id=alumno_id, clase_id=clase_id, fecha=hoy).first()
    
    if not ya_asistio:
        nueva = AsistenciaClase(alumno_id=alumno_id, clase_id=clase_id, fecha=hoy)
        alumno.asistencia += 1
        if alumno.clases_restantes > 0:
            alumno.clases_restantes -= 1
        db.session.add(nueva)
        db.session.commit()
        flash(f'Asistencia registrada para {alumno.nombre}', 'success')
    else:
        flash('Ya se registró asistencia hoy', 'warning')
    return redirect(url_for('clases'))

# ====================== PRODUCTOS Y VENTAS ======================

@app.route('/productos')
@login_required
def productos():
    lista = Producto.query.all()
    return render_template('productos.html', productos=lista)

@app.route('/ventas/nueva', methods=['POST'])
@login_required
def nueva_venta():
    prod_id = request.form.get('producto_id')
    prod = Producto.query.get(prod_id)
    if prod and prod.stock > 0:
        nueva = Venta(
            producto_id=prod.id,
            producto_nombre=prod.nombre,
            monto=prod.precio,
            usuario_id=current_user.id
        )
        prod.stock -= 1
        db.session.add(nueva)
        db.session.commit()
        flash('Venta registrada', 'success')
    return redirect(url_for('index'))

# ====================== AUTENTICACIÓN ======================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and check_password_hash(user.password, request.form.get('password')):
            login_user(user)
            return redirect(url_for('index'))
        flash('Credenciales incorrectas', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)