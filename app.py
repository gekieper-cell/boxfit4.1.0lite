import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Alumno, Clase, AsistenciaClase, Producto, Venta
from datetime import datetime, date, timedelta
from sqlalchemy import func, or_, text # Importamos text para la migración

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

# ====================== BLOQUE DE MIGRACIÓN AUTOMÁTICA ======================
def init_db():
    with app.app_context():
        db.create_all()
        # Intentar agregar columnas nuevas si no existen (PostgreSQL/SQLite)
        try:
            db.session.execute(text('ALTER TABLE alumnos ADD COLUMN fecha_vencimiento DATE'))
            db.session.commit()
            print("Columna fecha_vencimiento agregada.")
        except Exception:
            db.session.rollback() # La columna ya existía

        try:
            db.session.execute(text('ALTER TABLE alumnos ADD COLUMN ultimo_pago DATE'))
            db.session.commit()
            print("Columna ultimo_pago agregada.")
        except Exception:
            db.session.rollback()

# ====================== DASHBOARD ======================

@app.route('/')
@login_required
def index():
    hoy = date.today()
    
    total_alumnos = Alumno.query.filter_by(activo=True).count()
    alumnos_morosos = Alumno.query.filter_by(morosidad=True, activo=True).count()
    
    # Próximos vencimientos
    fecha_alerta = hoy + timedelta(days=7)
    
    # Usamos filtros seguros
    alumnos_vencidos = Alumno.query.filter(
        Alumno.activo == True,
        Alumno.fecha_vencimiento <= hoy
    ).count()

    alumnos_alerta = Alumno.query.filter(
        Alumno.activo == True,
        Alumno.fecha_vencimiento > hoy,
        Alumno.fecha_vencimiento <= fecha_alerta
    ).count()
    
    asistencias_hoy = AsistenciaClase.query.filter_by(fecha=hoy).count()
    dias_semana = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
    dia_actual = dias_semana[hoy.weekday()]
    clases_hoy = Clase.query.filter_by(dia=dia_actual).all()
    
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
    hoy = date.today()

    if filtro == 'deudores':
        query = query.filter_by(morosidad=True)
    elif filtro == 'vencimientos':
        proxima_semana = hoy + timedelta(days=7)
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
            
            fecha_vencimiento = ultimo_pago + timedelta(days=30)
            
            nuevo = Alumno(
                nombre=request.form['nombre'].upper(),
                dni=request.form['dni'],
                telefono=request.form.get('telefono', ''),
                fecha_inicio=fecha_inicio,
                ultimo_pago=ultimo_pago,
                fecha_vencimiento=fecha_vencimiento,
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

if __name__ == '__main__':
    init_db() # Llamamos a la función de inicialización y migración
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)