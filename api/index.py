import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Alumno, Clase, AsistenciaClase, Producto, Venta
from datetime import datetime, date, timedelta
from sqlalchemy import func, or_
from io import BytesIO

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

# ====================== INICIALIZACIÓN DE BASE DE DATOS ======================
with app.app_context():
    db.create_all()
    # Crear usuario admin por defecto si la tabla está vacía
    if not User.query.filter_by(username='admin').first():
        admin_user = User(
            username='admin',
            password=generate_password_hash('admin123'),
            role='admin'
        )
        db.session.add(admin_user)
        db.session.commit()
        print(">>> Usuario admin creado: admin / admin123")

login_manager = LoginManager(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ====================== RUTAS PRINCIPALES (DASHBOARD) ======================

@app.route('/')
@login_required
def index():
    hoy = date.today()
    total_alumnos = Alumno.query.filter_by(activo=True, estado='activo').count()
    alumnos_morosos = Alumno.query.filter_by(morosidad=True, activo=True, estado='activo').count()

    fecha_alerta = hoy + timedelta(days=7)
    alumnos_vencidos = Alumno.query.filter(Alumno.activo == True, Alumno.estado == 'activo', Alumno.fecha_vencimiento <= hoy).count()
    alumnos_alerta = Alumno.query.filter(Alumno.activo == True, Alumno.estado == 'activo', Alumno.fecha_vencimiento > hoy, Alumno.fecha_vencimiento <= fecha_alerta).count()

    asistencias_hoy = AsistenciaClase.query.filter_by(fecha=hoy).count()
    dias_semana = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
    clases_hoy = Clase.query.filter_by(dia=dias_semana[hoy.weekday()]).all()

    ultimos_alumnos = Alumno.query.filter_by(activo=True, estado='activo').order_by(Alumno.id.desc()).limit(10).all()
    productos = Producto.query.filter(Producto.stock > 0).all()
    
    return render_template('index.html', 
                         total_alumnos=total_alumnos,
                         alumnos_morosos=alumnos_morosos,
                         alumnos_vencidos=alumnos_vencidos,
                         alumnos_alerta=alumnos_alerta,
                         asistencias_hoy=asistencias_hoy,
                         clases_hoy=clases_hoy,
                         ultimos_alumnos=ultimos_alumnos,
                         productos=productos)

# ====================== RUTA DE LOGIN ======================
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

# ====================== RUTA DE LOGOUT ======================
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# ====================== RUTAS DE ALUMNOS ======================
@app.route('/alumnos')
@login_required
def listar_alumnos():
    alumnos = Alumno.query.filter_by(activo=True, estado='activo').order_by(Alumno.nombre).all()
    return render_template('alumnos.html', alumnos=alumnos)

@app.route('/alumno/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_alumno():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        telefono = request.form.get('telefono')
        email = request.form.get('email')
        fecha_vencimiento = request.form.get('fecha_vencimiento')
        
        nuevo = Alumno(
            nombre=nombre,
            telefono=telefono,
            email=email,
            fecha_registro=date.today(),
            fecha_vencimiento=datetime.strptime(fecha_vencimiento, '%Y-%m-%d').date() if fecha_vencimiento else None,
            activo=True,
            estado='activo',
            morosidad=False
        )
        db.session.add(nuevo)
        db.session.commit()
        flash('Alumno agregado exitosamente', 'success')
        return redirect(url_for('listar_alumnos'))
    
    return render_template('nuevo_alumno.html')

# ====================== RUTA PARA MARCAR ASISTENCIA ======================
@app.route('/asistencia', methods=['GET', 'POST'])
@login_required
def marcar_asistencia():
    if request.method == 'POST':
        alumno_id = request.form.get('alumno_id')
        clase_id = request.form.get('clase_id')
        
        asistencia = AsistenciaClase(
            alumno_id=alumno_id,
            clase_id=clase_id,
            fecha=date.today()
        )
        db.session.add(asistencia)
        db.session.commit()
        flash('Asistencia registrada', 'success')
        return redirect(url_for('index'))
    
    alumnos = Alumno.query.filter_by(activo=True, estado='activo').all()
    dias_semana = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
    clases = Clase.query.filter_by(dia=dias_semana[date.today().weekday()]).all()
    
    return render_template('asistencia.html', alumnos=alumnos, clases=clases)

# ====================== RUTAS DE PRODUCTOS ======================
@app.route('/productos')
@login_required
def listar_productos():
    productos = Producto.query.all()
    return render_template('productos.html', productos=productos)

@app.route('/producto/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_producto():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        precio = float(request.form.get('precio', 0))
        stock = int(request.form.get('stock', 0))
        
        producto = Producto(
            nombre=nombre,
            precio=precio,
            stock=stock
        )
        db.session.add(producto)
        db.session.commit()
        flash('Producto agregado', 'success')
        return redirect(url_for('listar_productos'))
    
    return render_template('nuevo_producto.html')

# ====================== RUTAS DE VENTAS ======================
@app.route('/ventas')
@login_required
def listar_ventas():
    ventas = Venta.query.order_by(Venta.fecha.desc()).all()
    return render_template('ventas.html', ventas=ventas)

@app.route('/venta/nueva', methods=['GET', 'POST'])
@login_required
def nueva_venta():
    if request.method == 'POST':
        producto_id = request.form.get('producto_id')
        cantidad = int(request.form.get('cantidad', 1))
        
        producto = Producto.query.get(producto_id)
        if producto and producto.stock >= cantidad:
            total = producto.precio * cantidad
            venta = Venta(
                producto_id=producto_id,
                cantidad=cantidad,
                total=total,
                fecha=date.today()
            )
            producto.stock -= cantidad
            db.session.add(venta)
            db.session.commit()
            flash('Venta registrada', 'success')
        else:
            flash('Stock insuficiente', 'danger')
        
        return redirect(url_for('listar_ventas'))
    
    productos = Producto.query.filter(Producto.stock > 0).all()
    return render_template('nueva_venta.html', productos=productos)

# ====================== MANEJADOR PARA VERCEL ======================
application = app

# ====================== EJECUCIÓN LOCAL ======================
if __name__ == '__main__':
    app.run(debug=True)
