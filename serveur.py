import os
import json
import uuid
from datetime import datetime, time
from flask import Flask, request, jsonify, Response, send_file
from flask_cors import CORS
import base64
from werkzeug.utils import secure_filename
import threading
import time as time_module

app = Flask(__name__)
CORS(app)  # Active CORS pour toutes les routes

# Configuration
app.config['SECRET_KEY'] = 'votre_cle_secrete_ici'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Créer le dossier de uploads s'il n'existe pas
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Structures de données pour stocker les informations
users = []
files = []
chat_messages = []
connected_users = set()
sse_listeners = []

# Horaires de service (7h-22h)
SERVICE_START_HOUR = 7
SERVICE_END_HOUR = 22

# Charger les données existantes si disponibles
def load_data():
    global users, files, chat_messages
    try:
        with open('users.json', 'r') as f:
            users = json.load(f)
    except FileNotFoundError:
        users = []
    
    try:
        with open('files.json', 'r') as f:
            files = json.load(f)
    except FileNotFoundError:
        files = []
    
    try:
        with open('chat_messages.json', 'r') as f:
            chat_messages = json.load(f)
    except FileNotFoundError:
        chat_messages = []

# Sauvegarder les données
def save_data():
    with open('users.json', 'w') as f:
        json.dump(users, f)
    
    with open('files.json', 'w') as f:
        json.dump(files, f)
    
    with open('chat_messages.json', 'w') as f:
        json.dump(chat_messages, f)

# Vérifier si le service est disponible selon les horaires
def is_service_available():
    now = datetime.now()
    current_hour = now.hour
    return SERVICE_START_HOUR <= current_hour < SERVICE_END_HOUR

# Middleware pour vérifier les horaires de service
@app.before_request
def check_service_hours():
    if not is_service_available():
        return jsonify({
            'error': 'Service indisponible',
            'message': f'Le service est disponible de {SERVICE_START_HOUR}h à {SERVICE_END_HOUR}h'
        }), 503

# Routes d'authentification
@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    name = data.get('name')
    email = data.get('email')
    password = data.get('password')
    confirm_password = data.get('confirm_password')
    
    if not all([name, email, password, confirm_password]):
        return jsonify({'error': 'Tous les champs sont requis'}), 400
    
    if password != confirm_password:
        return jsonify({'error': 'Les mots de passe ne correspondent pas'}), 400
    
    if any(user['email'] == email for user in users):
        return jsonify({'error': 'Un compte avec cet email existe déjà'}), 400
    
    new_user = {
        'id': str(uuid.uuid4()),
        'name': name,
        'email': email,
        'password': password,  # En production, hasher le mot de passe
        'created_at': datetime.now().isoformat()
    }
    
    users.append(new_user)
    save_data()
    
    return jsonify({
        'message': 'Compte créé avec succès',
        'user': {k: v for k, v in new_user.items() if k != 'password'}
    }), 201

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    
    if not email or not password:
        return jsonify({'error': 'Email et mot de passe requis'}), 400
    
    user = next((u for u in users if u['email'] == email and u['password'] == password), None)
    
    if not user:
        return jsonify({'error': 'Email ou mot de passe incorrect'}), 401
    
    # Ajouter l'utilisateur aux utilisateurs connectés
    connected_users.add(user['id'])
    notify_user_joined(user)
    
    return jsonify({
        'message': 'Connexion réussie',
        'user': {k: v for k, v in user.items() if k != 'password'}
    })

@app.route('/api/logout', methods=['POST'])
def logout():
    data = request.json
    user_id = data.get('user_id')
    
    if user_id in connected_users:
        connected_users.remove(user_id)
        user = next((u for u in users if u['id'] == user_id), None)
        if user:
            notify_user_left(user)
    
    return jsonify({'message': 'Déconnexion réussie'})

# Routes pour les fichiers
@app.route('/api/files', methods=['GET'])
def get_user_files():
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'error': 'ID utilisateur requis'}), 400
    
    user_files = [f for f in files if f['owner'] == user_id]
    return jsonify({'files': user_files})

@app.route('/api/files/shared', methods=['GET'])
def get_shared_files():
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'error': 'ID utilisateur requis'}), 400
    
    shared_files = [f for f in files if f['shared'] and f['owner'] != user_id]
    return jsonify({'files': shared_files})

@app.route('/api/upload', methods=['POST'])
def upload_file():
    user_id = request.form.get('user_id')
    if not user_id:
        return jsonify({'error': 'ID utilisateur requis'}), 400
    
    if 'file' not in request.files:
        return jsonify({'error': 'Aucun fichier fourni'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Nom de fichier invalide'}), 400
    
    filename = secure_filename(file.filename)
    file_id = str(uuid.uuid4())
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], file_id + '_' + filename)
    
    file.save(file_path)
    
    # Lire le contenu du fichier en base64
    with open(file_path, 'rb') as f:
        file_content = base64.b64encode(f.read()).decode('utf-8')
    
    new_file = {
        'id': file_id,
        'name': filename,
        'size': os.path.getsize(file_path),
        'type': file.content_type,
        'content': f"data:{file.content_type};base64,{file_content}",
        'owner': user_id,
        'shared': False,
        'created_at': datetime.now().isoformat(),
        'path': file_path
    }
    
    files.append(new_file)
    save_data()
    
    return jsonify({
        'message': 'Fichier uploadé avec succès',
        'file': new_file
    }), 201

@app.route('/api/files/<file_id>', methods=['PUT'])
def update_file(file_id):
    data = request.json
    shared = data.get('shared')
    
    file = next((f for f in files if f['id'] == file_id), None)
    if not file:
        return jsonify({'error': 'Fichier non trouvé'}), 404
    
    file['shared'] = shared
    save_data()
    
    return jsonify({'message': 'Fichier mis à jour avec succès'})

@app.route('/api/files/<file_id>', methods=['DELETE'])
def delete_file(file_id):
    file = next((f for f in files if f['id'] == file_id), None)
    if not file:
        return jsonify({'error': 'Fichier non trouvé'}), 404
    
    # Supprimer le fichier du système de fichiers
    if os.path.exists(file['path']):
        os.remove(file['path'])
    
    files.remove(file)
    save_data()
    
    return jsonify({'message': 'Fichier supprimé avec succès'})

@app.route('/api/download/<file_id>', methods=['GET'])
def download_file(file_id):
    file = next((f for f in files if f['id'] == file_id), None)
    if not file:
        return jsonify({'error': 'Fichier non trouvé'}), 404
    
    if not os.path.exists(file['path']):
        return jsonify({'error': 'Fichier non disponible'}), 404
    
    return send_file(file['path'], as_attachment=True, download_name=file['name'])

# Routes pour le chat
@app.route('/api/chat/messages', methods=['GET'])
def get_chat_messages():
    return jsonify({'messages': chat_messages[-50:]})  # Retourne les 50 derniers messages

@app.route('/api/chat/send', methods=['POST'])
def send_chat_message():
    data = request.json
    user_id = data.get('user_id')
    message = data.get('message')
    
    if not user_id or not message:
        return jsonify({'error': 'ID utilisateur et message requis'}), 400
    
    user = next((u for u in users if u['id'] == user_id), None)
    if not user:
        return jsonify({'error': 'Utilisateur non trouvé'}), 404
    
    new_message = {
        'id': str(uuid.uuid4()),
        'user': user['name'],
        'userId': user_id,
        'message': message,
        'time': datetime.now().isoformat(),
        'type': 'outgoing'
    }
    
    chat_messages.append(new_message)
    save_data()
    
    # Notifier tous les écouteurs SSE du nouveau message
    notify_new_message(new_message)
    
    return jsonify({'message': 'Message envoyé', 'chat_message': new_message})

# Routes pour les utilisateurs en ligne
@app.route('/api/users/online', methods=['GET'])
def get_online_users():
    online_users_list = [u for u in users if u['id'] in connected_users]
    return jsonify({'users': online_users_list})

# SSE (Server-Sent Events) pour les mises à jour en temps réel
def notify_new_message(message):
    for listener in sse_listeners:
        try:
            listener(f"data: {json.dumps({'type': 'message', **message})}\n\n")
        except:
            # Supprimer les écouteurs qui ne répondent plus
            sse_listeners.remove(listener)

def notify_user_joined(user):
    for listener in sse_listeners:
        try:
            listener(f"data: {json.dumps({'type': 'user_joined', 'user': user})}\n\n")
        except:
            sse_listeners.remove(listener)

def notify_user_left(user):
    for listener in sse_listeners:
        try:
            listener(f"data: {json.dumps({'type': 'user_left', 'user': user})}\n\n")
        except:
            sse_listeners.remove(listener)

def notify_user_list():
    online_users_list = [u for u in users if u['id'] in connected_users]
    for listener in sse_listeners:
        try:
            listener(f"data: {json.dumps({'type': 'user_list', 'users': online_users_list})}\n\n")
        except:
            sse_listeners.remove(listener)

@app.route('/api/events')
def sse_events():
    def event_stream():
        sse_listeners.append(lambda data: yield data)
        # Envoyer périodiquement la liste des utilisateurs en ligne
        while True:
            notify_user_list()
            time_module.sleep(10)
    
    return Response(event_stream(), mimetype='text/event-stream')

# Route pour vérifier l'état du service
@app.route('/api/status', methods=['GET'])
def service_status():
    current_time = datetime.now()
    service_available = is_service_available()
    
    return jsonify({
        'service_available': service_available,
        'current_time': current_time.isoformat(),
        'service_hours': {
            'start': SERVICE_START_HOUR,
            'end': SERVICE_END_HOUR
        }
    })

# Route pour formater la taille des fichiers
@app.route('/api/utils/format-size', methods=['POST'])
def format_file_size():
    data = request.json
    bytes_size = data.get('bytes')
    
    if bytes_size is None:
        return jsonify({'error': 'Bytes size required'}), 400
    
    if bytes_size == 0:
        return jsonify({'formatted': '0 Bytes'})
    
    k = 1024
    sizes = ['Bytes', 'KB', 'MB', 'GB']
    i = max(0, min(len(sizes)-1, int(math.floor(math.log(bytes_size) / math.log(k)))))
    
    formatted = f"{bytes_size / math.pow(k, i):.2f} {sizes[i]}"
    return jsonify({'formatted': formatted})

# Point d'entrée de l'application
if __name__ == '__main__':
    load_data()
    app.run(debug=True, port=5000)
