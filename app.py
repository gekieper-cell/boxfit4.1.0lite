import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Alumno, Clase, AsistenciaClase, Producto, Venta
from datetime import datetime, date, timedelta
from sqlalchemy import func, or_, text

app = Flask(__name__)

# Configuración de Seguridad y DB
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
            f_inicio = request.form.get('fecha_inicio')
            fecha_inicio = datetime.strptime(f_inicio, '%Y-%m-%d').date() if f_inicio else date.today()
            
            nuevo = Alumno(
                nombre=request.form['nombre'].upper(),
                dni=request.form['dni'],
                telefono=request.form.get('telefono', ''),
                fecha_inicio=fecha_inicio,
                fecha_vencimiento=fecha_inicio + timedelta(days=30),
                tipo_clase=request.form.get('tipo_clase'),
                valor_cuota=float(request.form.get('valor_cuota', 15000)),
                clases_totales=int(request.form.get('clases_totales', 0)),
                clases_restantes=int(request.form.get('clases_totales', 0)),
                activo=True
            )
            db.session.add(nuevo)
            db.session.commit()
            flash(f'Alumno {nuevo.nombre} registrado', 'success')
            return redirect(url_for('alumnos'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {str(e)}', 'error')
    return render_template('nuevo_alumno.html')

@app.route('/alumnos/registrar_pago/<int:id>', methods=['POST'])
@login_required
def registrar_pago(id):
    alumno = Alumno.query.get_or_404(id)
    alumno.ultimo_pago = date.today()
    alumno.fecha_vencimiento = date.today() + timedelta(days=30)
    alumno.morosidad = False
    if alumno.clases_totales > 0:
        alumno.clases_restantes = alumno.clases_totales
    db.session.commit()
    flash('Pago registrado correctamente', 'success')
    return redirect(url_for('alumnos'))

# ====================== CLASES Y ASISTENCIA ======================

@app.route('/clases')
@login_required
def clases():
    todas_clases = Clase.query.all()
    alumnos_activos = Alumno.query.filter_by(activo=True).order_by(Alumno.nombre).all()
    return render_template('clases.html', clases=todas_clases, alumnos=alumnos_activos)

@app.route('/clases/nueva', methods=['POST'])
@login_required
def nueva_clase():
    nueva = Clase(
        nombre=request.form['nombre'],
        dia=request.form['dia'],
        hora=request.form['hora'],
        capacidad=int(request.form.get('capacidad', 20))
    )
    db.session.add(nueva)
    db.session.commit()
    flash('Clase creada correctamente', 'success')
    return redirect(url_for('clases'))

@app.route('/asistencia/registrar', methods=['POST'])
@login_required
def registrar_asistencia():
    alumno_id = request.form.get('alumno_id')
    clase_id = request.form.get('clase_id')
    alumno = Alumno.query.get(alumno_id)
    
    ya_asistio = AsistenciaClase.query.filter_by(alumno_id=alumno_id, fecha=date.today()).first()
    if ya_asistio:
        flash('El alumno ya registró asistencia hoy', 'warning')
    else:
        asistencia = AsistenciaClase(alumno_id=alumno_id, clase_id=clase_id, fecha=date.today())
        alumno.asistencia += 1
        if alumno.clases_restantes > 0:
            alumno.clases_restantes -= 1
        db.session.add(asistencia)
        db.session.commit()
        flash(f'Asistencia grabada para {alumno.nombre}', 'success')
    return redirect(url_for('clases'))

# ====================== VENTAS Y PRODUCTOS ======================

@app.route('/ventas')
@login_required
def ventas():
    historial = Venta.query.order_by(Venta.fecha.desc()).all()
    return render_template('ventas.html', ventas=historial)

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
        venta = Venta(
            producto_id=prod.id,
            producto_nombre=prod.nombre,
            monto=prod.precio,
            usuario_id=current_user.id
        )
        prod.stock -= 1
        db.session.add(venta)
        db.session.commit()
        flash('Venta registrada', 'success')
    return redirect(url_for('index'))

@app.route('/venta_rapida', methods=['POST'])
@login_required
def venta_rapida():
    return nueva_venta()

# ====================== USUARIOS ======================

@app.route('/usuarios')
@login_required
def usuarios():
    lista = User.query.all()
    return render_template('usuarios.html', usuarios=lista)

@app.route('/usuarios/nuevo', methods=['POST'])
@login_required
def nuevo_usuario():
    username = request.form.get('username')
    password = request.form.get('password')
    if User.query.filter_by(username=username).first():
        flash('El usuario ya existe', 'error')
    else:
        nuevo = User(
            username=username,
            password=generate_password_hash(password),
            role=request.form.get('role', 'operador')
        )
        db.session.add(nuevo)
        db.session.commit()
        flash(f'Usuario {username} creado', 'success')
    return redirect(url_for('usuarios'))

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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        # Migración automática de columnas para Railway
        try:
            db.session.execute(text('ALTER TABLE alumnos ADD COLUMN fecha_vencimiento DATE'))
            db.session.commit()
        except:
            db.session.rollback()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)