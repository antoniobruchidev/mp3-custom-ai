import datetime
import os
from flask import flash, redirect, render_template, request, url_for
from celery.result import AsyncResult
import requests
from custom_assistant import app, db, argon2
from custom_assistant.inference import chat
from custom_assistant.mail import forgot_password_email, send_activation_email
from custom_assistant.models import Assistant, CharacterTrait, Collection, User, BackgroundIngestionTask
from flask_login import AnonymousUserMixin, LoginManager, current_user, login_required, login_user, logout_user
from sqlalchemy.exc import OperationalError
from psycopg2.errors import NotNullViolation
from custom_assistant.models import Source
from custom_assistant.storage import get_files, upload_file
from worker.utils import save_file, get_proprietary_hardware_status
from worker.tasks import celery, add, retry


login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

@login_manager.user_loader
def load_user(user_id):
    try:
        user = db.session.get(User, user_id)
        return user
    except OperationalError:
        try:
            user = db.session.get(User, user_id)
        except:
            return AnonymousUserMixin()
        

# Test routes

@app.get("/result/<id>")
def task_result(id: str) -> dict[str, object]:
    result = AsyncResult(id, app=celery)
    return {
        "ready": result.ready(),
        "successful": result.successful(),
        "value": result.result if result.ready() else None,
    }


@app.post("/add")
def start_add() -> dict[str, object]:
    a = request.form.get("a", type=int)
    b = request.form.get("b", type=int)
    try:
        result = add.delay(a, b)
    except:
        return {"error": "ERROR!"}
    return {"result_id": result.id}


@app.post("/chat")
def bot_answer() -> dict:
    """Method to invoke the chatbot

    Returns:
        dict: answer and tokens usage
    """
    prompt = request.form.get("base-prompt", None)
    traits = request.form.get("traits", None)
    message= request.form.get("message", None)
    question = request.form.get("question", None)
    collection_id = request.form.get("collection-id", None)
    print(question,collection_id)
    if question is not None and collection_id is not None:
        answer = chat(question=question, collection_id=collection_id)
        prompt_tokens = None
        comp_tokens = None
    else:
        answer, prompt_tokens, comp_tokens = chat(
            prompt, message, traits
        )
    return {
        "status": 200,
        "answer": answer,
        "prompt_tokens": prompt_tokens,
        "comp_tokens": comp_tokens
        }


@app.errorhandler(404)
def page_not_found(e):
    return {
        "page": "not found",
        "status": "404"
    }

       
@app.get("/")
def home():
    """Route to home

    Returns:
        render_template: homepage
    """
    return render_template("home.html")


@app.get("/playground")
def playground():
    """Route to playground

    Returns:
        render_template: playground
    """
    assistants = []
    traits = []
    assistants_available = 0
    traits_available = 0
    try:
        if current_user.is_authenticated:
            assistants = db.session.query(Assistant).filter(
                Assistant.user_id==current_user.id
            ).all()
            traits = db.session.query(CharacterTrait).filter(
                CharacterTrait.user_id==current_user.id
            ).all()
        assistants_available = int(os.getenv("ASSISTANT_SLOTS", 5)) - len(
            assistants
        )
        traits_available = int(os.getenv("TRAIT_SLOTS", 20)) - len(traits)
        print(assistants_available, traits_available)
        return render_template(
            "playground.html",
            user=current_user,
            assistants_available=assistants_available,
            traits_available=traits_available,
            assistants_limit=os.getenv("ASSISTANT_SLOTS", 5),
            traits_limit=os.getenv("TRAIT_SLOTS", 20),
            assistants=assistants,
            traits=traits
            )
    except OperationalError as e:
        db.session.rollback()
        db.session.close()
        print(e)
        return redirect(playground)
        


@app.post("/assistants/create")
@login_required
def create_or_edit_assistant():
    """Method to create an assistant

    Returns:
        {json}: message or error
    """
    if not request.is_json:
        return {"status": 400, "error": "Bad request"}
    data = request.get_json()
    trait_limit = int(os.getenv("TRAIT_SLOTS", 20))
    assistant_limit = int(os.getenv("ASSISTANT_SLOTS", 5))
    print(data)
    try:
        if data['edit']:
            assistant = db.session.get(Assistant, data['assistant_id'])
            assistant.name = data['assistant_name']
            assistant.prompt = data['base_prompt']
            db.session.add(assistant)
            db.session.commit()
            db.session.close()
            flash("Assistant modified successfully")
            return {"status": 200}
        user = db.session.get(User, current_user.id)
        user_traits = db.session.query(CharacterTrait).filter(
            CharacterTrait.user_id==user.id
        ).all()
        user_assistants = db.session.query(Assistant).filter(
            Assistant.user_id==user.id
        ).all()
    except OperationalError as e:
        db.session.rollback()
        db.session.close()
        return {"status": 500, "error": f"Please try again... Operational error: {e}"}
    except Exception as e:
        db.session.rollback()
        db.session.close()
        return {"status": 500, "error": f"Please try again... Operational error: {e}"}
        
    assistant_names = [assistant.name for assistant in user_assistants]
    trait_names = [f"{trait.trait}: {trait.value}" for trait in user_traits]
    traits_to_be_saved = []
    for trait in data['traits']:
        print(trait)
        trait_value = trait['value'].replace("\n", "").replace(" ", "")
        t = f"{trait['trait']}: {trait_value}"
        traits_to_be_saved.append(t)
    for trait in traits_to_be_saved:
        if trait in trait_names:
            index = traits_to_be_saved.index(trait)
            traits_to_be_saved.pop(index)
    if data['assistant_name'] in assistant_names:
        return {"status": 400, "error": "You already have an assistant with that name"}
    if assistant_limit == len(user_assistants):
        print(assistant_limit, user_assistants)
        return {"status": 400, "error": "Not enough assistant slots available"}
    if trait_limit == len(user_traits) + len(data['traits']):
        print(trait_limit, user_traits)
        return {"status": 400, "error": "Not enough trait slots available"}
    try:
        assistant = Assistant(
            user_id=user.id,
            name=data['assistant_name'],
            prompt=data['base_prompt']
        )
        db.session.add(assistant)
        db.session.commit()
        db.session.close()
        for trait in data['traits']:
            trait_value = trait['value'].replace("\n", "").replace(" ", "")
            t = f"{trait['trait']}: {trait_value}"
            if t in traits_to_be_saved:
                print(user.id, trait['trait'], trait_value)
                character_trait = CharacterTrait(
                    user_id=user.id,
                    trait=trait['trait'],
                    value=trait_value,
                    reason_why=trait['reason_why']
                )
                db.session.add(character_trait)
                db.session.commit()
                db.session.close()
            else:
                character_trait = db.session.query(CharacterTrait).filter(
                    CharacterTrait.user_id==user.id,
                    CharacterTrait.trait==trait['trait'],
                    CharacterTrait.value==trait_value
                ).first()
            assistant.traits.append(character_trait)
            db.session.add(assistant)
            db.session.commit()
            db.session.close()
    except OperationalError as e:
        db.session.rollback()
        db.session.close()
        return {"status": 500, "error": f"Please try again... Operational error: {e}"}
    except Exception as e:
        db.session.rollback()
        db.session.close()
        return {"status": 500, "error": e}
    flash(f"name: {data['assistant_name']} - base prompt: {data['base_prompt']} - traits: {data['traits']}")
    return {"status": 200}


@app.get("/collections")
@login_required
def get_collections():
    """Route to collections

    Returns:
        render_template: collections
    """
    collections_limit = os.getenv("COLLECTION_SLOTS", 3)
    sources_limit = os.getenv("SOURCES_SLOTS", 10)
    timestamp = app.config["PROPRIETARY_HARDWARE_DOWN"]
    chat_server, embedding_server = get_proprietary_hardware_status()
    if not embedding_server:
        if timestamp == 0:
            app.config["PROPRIETARY_HARDWARE_DOWN"] = datetime.datetime.now().timestamp()
        timestamp = datetime.datetime.fromtimestamp(
            app.config["PROPRIETARY_HARDWARE_DOWN"]
            ).isoformat()
    else:
        app.config["PROPRIETARY_HARDWARE_DOWN"] = 0
    if current_user.is_authenticated:
        try:
            collections = db.session.query(Collection).filter(
                Collection.user_id==current_user.id
            ).all()
            c_sources = [collection.sources for collection in collections]
            collections_with_sources = zip(collections, c_sources)
            sources = db.session.query(Source).filter(
                Source.user_id==current_user.id
            ).all()
            
        except OperationalError as e:
            db.session.rollback()
            db.session.close()
            return redirect(url_for('get_collections'))
        collections_available = collections_limit - len(collections)
        sources_available = sources_limit - len(sources)
        return render_template(
            "collections.html",
            collections_limit=collections_limit,
            sources_limit=sources_limit,
            collections_with_sources=collections_with_sources,
            collections_available=collections_available,
            sources_available=sources_available,
            sources=sources,
            timestamp=timestamp
        )
    else:
        return redirect(url_for("login"))


@app.route("/register", methods=['GET', 'POST'])
def register():
    """Route to create a user

    Returns:
        json | render_template: user id if successfull,
        missing data if not | register page
    """
    g_client_id = os.getenv("GOOGLE_CLIENT_ID")
    if request.method == "POST":
        email = request.form.get("email")
        user = None
        try:
            user = User(email=email).sign_up_with_email(
                request.form.get("password", None),
                request.form.get("confirm-password", None)
            )
        except OperationalError as e:
            db.session.rollback()
            db.session.close()
            error = f"Operational error - please retry..."
            return render_template("register.html", g_client_id=g_client_id, error=error)
        if user is not None:
            db.session.add(user)
            db.session.commit()
            db.session.close()
            send_activation_email(user)
            error = f"An email has been sent to {user.email}. Please verify your email address."
            return render_template("login.html", g_client_id=g_client_id, error=error)
        else:
            error = f"Email {user.email} already present"
            return render_template(
                "login.html",
                error=error,
                g_client_id=g_client_id
            )
    else:
        return render_template("register.html", g_client_id=g_client_id)


@app.get("/verify/<user_id>")
def verify_user(user_id):
    """Route to verify user email address

    Args:
        user_id (int): the user id

    Returns:
        redirect: login
    """
    g_client_id = os.getenv("GOOGLE_CLIENT_ID")
    try:
        user = db.session.get(User, user_id)
        user.verified = True
        db.session.add(user)
        db.session.commit()
        db.session.close()
    except OperationalError as e:
        db.session.rollback()
        db.session.close()
        error = f"Operational error: {e} - please retry..."
        return render_template("login.html", g_client_id=g_client_id, error=error)
    return render_template("login.html", g_client_id=g_client_id, error="Account verified.")
    

@app.route("/login", methods=['GET', 'POST'])
def login():
    """Route to login

    Returns:
        render_template: login page if method is get
        json: if post
    """
    g_client_id = os.getenv("GOOGLE_CLIENT_ID")
    try:
        if request.method == "POST":
            google_id = request.form.get("google-id", None)
            email = request.form.get("email", None)
            if google_id is not None:
                user = db.session.query(User).filter(
                    User.google_id==google_id
                ).first()
                if user is None:
                    user = User(
                        google_id=google_id,
                        email=email
                    ).sign_up_with_google()
                    db.session.add(user)
                    db.session.commit()
                    db.session.close()
                    login_user(user)
                    flash("registered with google")
                    return {"status": 200}
                else:
                    login_user(user)
                    flash("logged in with google")
                    return {"status": 200}
            else:
                user = db.session.query(User).filter(User.email == email).first()
                if user is not None:
                    if user.verified:
                        password_check = user.check_password(
                            request.form.get("password", None)
                        )
                        
                        if password_check:
                            login_user(user)
                            flash("logged in")
                            return redirect(url_for("home"))
                        else:
                            error = "Invalid credentials"
                            return render_template(
                                "login.html",
                                error=error,
                                g_client_id=g_client_id
                            )
                    else:
                        send_activation_email(user)
                        error = f"""User not verified - please verify 
                        from the email sent at {user.email}"""
                        return render_template(
                                "login.html",
                                error=error,
                                g_client_id=g_client_id
                            )
                else:
                    error = "Email not found"
                    return render_template(
                                "login.html",
                                error=error,
                                g_client_id=g_client_id
                            )
        return render_template("login.html", g_client_id=g_client_id)
    except OperationalError as e:
        db.session.rollback()
        db.session.close()
        return render_template(
            "login.html",
            g_client_id=g_client_id,
            error=f"Operational Error: {e} - Please retry..."
        )
    

@app.get("/forgot_password/<email>")
def forgot_password(email):
    """Route to request a change of password via email

    Args:
        email (str): the user email address

    Returns:
        json: a message or an error
    """
    try:
        user = db.session.query(User).filter(User.email==email).first()
        if user is not None:
            user.forgot_passwd_url = forgot_password_email(user)
            db.session.add(user)
            db.session.commit()
            db.session.close()
            return {
                "status": 200,
                "message": "An email has been sent to you to change your password."}
        else:
            return {"status": 404, "error": "Email not found"}
    except OperationalError as e:
        db.session.rollback()
        db.session.close()
        return {"status": 500, "error": "Operational error: {e} - Please retry..."}


@app.route("/change_password/<hash>", methods=["GET", "POST"])
def change_password(hash):
    """Method to change the user password:

    Args:
        hash (str): the hash created during forgot password

    Returns:
        render_template: change password if get
        redirect: login or home if post
    """
    g_client_id = os.getenv("GOOGLE_CLIENT_ID")
    try:
        user = db.session.query(User).filter(User.forgot_passwd_url==hash).first()
    except OperationalError:
        db.session.rollback()
        db.session.close()
        return redirect(url_for('change_password', hash=hash))
    if user is None:
        message = "No users password change at this url"
        return render_template(
            "login.html",
            error=message,
            g_client_id=g_client_id
        )
    else:    
        if request.method == "POST":
            try:
                user.password = argon2.generate_password_hash(
                    request.form.get("password")
                )
                db.session.add(user)
                db.session.commit()
                db.session.close()
                message = "Password changed correctly"
            except OperationalError as e:
                db.session.rollback()
                db.session.close()
                message = "Operational error: {e} - Please retry..."
                return render_template(
            "login.html",
            error=message,
            g_client_id=g_client_id
        )
            return render_template(
                "login.html",
                g_client_id=g_client_id,
                error=message
            )
        else:
            return render_template(
                "forgot_password.html", hash=hash, user=user
            )

@app.route("/logout")
@login_required
def logout():
    """Route to logout

    Returns:
        redirect: home
    """
    logout_user()
    return redirect(url_for('home'))


@app.get("/proprietary_hardware_status")
def get_status():
    """Method to get the proprietary hardware status - Test route
    """
    chat_server, embedding_server = get_proprietary_hardware_status()
    if not embedding_server:
        if app.config["PROPRIETARY_HARDWARE_DOWN"] == 0:
            app.config["PROPRIETARY_HARDWARE_DOWN"] = datetime.datetime.now().timestamp()
    else:
        app.config["PROPRIETARY_HARDWARE_DOWN"] = 0
    return {
        "status": 200,
        "chat_server": chat_server,
        "embedding_server": embedding_server
    }


@app.post("/collections/create")
@login_required
def create_collection():
    """Route to create a collection

    Returns:
        dict: collection id
    """
    collection_name = request.form.get("collection-name")
    collection_id = request.form.get("collection-id")
    description = request.form.get("collection-description", None)
    collection_limit = os.getenv("COLLECTION_SLOTS", 3)
    print(collection_id, collection_name)
    if description is None or description == "":
        flash("error")
        return redirect(url_for("collections"))
    try:
        user_id = current_user.id
        if collection_id != "":
            collection = db.session.get(Collection, collection_id)
            collection.collection_name = collection_name
            collection.documents_description = description
            db.session.add(collection)
            db.session.commit()
            flash(f"Collection {collection.collection_name} updated successfully")
            db.session.close()
            return redirect(url_for("get_collections"))
        else:
            collections = db.session.query(Collection).filter(
                Collection.user_id==user_id
            ).all()
            names = [collection.collection_name for collection in collections]
            if len(collections) == collection_limit:
                flash("You already have reached the maximum collecections possibile. Delete one first.")
                db.session.close()
                return redirect(url_for("get_collections"))
            if collection_name in names:
                flash("You already have a collection with that name.")
                return redirect(url_for("get_collections"))
            collection = Collection(
                collection_name=request.form.get("collection-name"),
                documents_description=request.form.get("collection-description"),
                user_id=user_id
            )
            db.session.add(collection)
            db.session.commit()
            flash(f"Added collection {collection.id}")
            db.session.close()
            return redirect(url_for("get_collections"))
    except OperationalError as e:
        db.session.rollback()
        db.session.close()
        flash(f"Operational error: {e} - Please retry...")
        return redirect(url_for("get_collections"))
    except Exception as e:
        db.session.rollback()
        db.session.close()
        flash(f"Unknown error: {e} - Please retry...")
        return redirect(url_for("get_collections"))


@app.post("/sources/create")
@login_required
def create_source():
    """Route to create a source

    Returns:
        dict: source id
    """
    try:
        user_id = current_user.id
    except OperationalError as e:
        db.session.rollback()
        db.session.close()
        return {
            "status": 500,
            "error": f"Operational error: {e} - Please retry..."
        }
    saved, filename = save_file(request, user_id)
    description = request.form.get("description", None)
    name = request.form.get("source-name", None)
    if saved:
        try:
            source = db.session.query(Source).filter(
                Source.filename==filename,
                Source.user_id==user_id
            ).first()
            aws_key = f"{user_id}/{filename}"
            keys = get_files()
            for key in keys:
                if aws_key in key and source is not None:
                    return {
                        "status": 400,
                        "error": "Source already present, please refresh the page."}
            source = Source(
                filename=filename,
                user_id=user_id,
                aws_key=aws_key,
                description=description,
                name=name
            )
            db.session.add(source)
            db.session.commit()
            source_id = source.id
            db.session.close()
            assert upload_file(aws_key)
            print("uploaded to aws")
        except OperationalError as e:
            db.session.rollback()
            db.session.close()
            return {"status": 500, "error": f"Operational error: {e} - Please retry..."}
        except NotNullViolation as e:
            db.session.rollback()
            db.session.close()
            return {"status": 500, "error": f"Missing data: {e} - Please retry..."}
        except Exception as e:
            db.session.rollback()
            db.session.close()
            return {"status": 500, "error": f"Unknown error: {e}"}
        return {"status": 200, "message": f"Added source: {source_id}"}
    else:
        return {"status": 400, "error": "Error saving the file"}


@app.post("/add_trait_to_assistant/<trait_id>/<assistant_id>")
@login_required
def add_trait_to_assistant(trait_id, assistant_id):
    """Method to add a trait to an assistant

    Returns:
        dict: status and message/error
    """
    try:
        assistant = db.session.get(Assistant, assistant_id)
        trait = db.session.get(CharacterTrait, trait_id)
        traits = [t.trait for t in assistant.traits]
        if trait.trait == traits:
            return {
                "status": 400,
                "error": "You already have a trait with that exact name."
            }
        else:
            assistant.traits.append(trait)
            db.session.add(assistant)
            db.session.commit()
            message = f"{trait.trait}: {trait.value} added to {assistant.name}"
            db.session.close()
            return {"status": 200, "message": message}
    except OperationalError:
        error = f"Operational error: {e} - Please retry"
    except Exception as e:
        error = f"Unknown error: {e} - Please retry"
    return {"status": 500, "error": error}
        
            
@app.post("/add_source_to_collection")
@login_required
def add_source_to_collection():
    """Method to add a source to a collection

    Returns:
        dict: status and message/error
    """
    source_id = request.form.get("source-id")
    collection_id = request.form.get("collection-id")
    try:
        collection = db.session.get(Collection, collection_id)
        ids = [source.id for source in collection.sources]
        tasks = db.session.query(BackgroundIngestionTask).filter(
            BackgroundIngestionTask.collection_id==collection_id,
            BackgroundIngestionTask.source_id==source_id,
            BackgroundIngestionTask.ended==False
        ).first()
        if int(source_id) in ids:
            db.session.close()
            return {"stauts": 400, "error": "Source already in collection"}
        if tasks is not None:
            db.session.close()
            return {"status": 400, "error": "Already planned the source ingestion in the collection, please wait an email will be sent to you when finished."}
        task = BackgroundIngestionTask(
            collection_id=collection_id,
            source_id=source_id,
        )
        db.session.add(task)
        db.session.commit()
        task_id = task.id
        db.session.close()
    except OperationalError as e:
        db.session.rollback()
        db.session.close()
        print(f"Operational error: {e} - Retrying")
        try:
            task = BackgroundIngestionTask(
                collection_id=collection_id,
                source_id=source_id,
            )
            db.session.add(task)
            db.session.commit()
            task_id = task.id
            db.session.close()
        except OperationalError as e:
            db.session.rollback()
            db.session.close()
            print(f"Operational error: {e} - Stop")
            return {"status": 500, "error": e}
    chat_server, embedding_server = get_proprietary_hardware_status()
    if embedding_server:
        app.config["PROPRIETARY_HARDWARE_DOWN"] = 0
        url = f"{os.getenv('PROPRIETARY_HARDWARE_URL')}/ingest_data"
        payload = {
            "task_id": task_id,
            "secret_key": os.getenv("PROPRIETARY_HARDWARE_SECRET_KEY")
        }
        response = requests.post(url, json=payload)
        data = response.json()
        if response.status_code == 200:
            return {
                "message": f"Started job {data['message']}",
                "status": 200
            }
    else:
        if app.config["PROPRIETARY_HARDWARE_DOWN"] == 0:
            app.config["PROPRIETARY_HARDWARE_DOWN"] = datetime.datetime.now().timestamp()
        result = retry.delay(task.id)
        try:
            task = db.session.get(BackgroundIngestionTask, task_id)
            task.heroku_task_id = result.id
            db.session.add(task)
            db.session.commit()
            db.session.close()
        except OperationalError as e:
            db.session.rollback()
            db.session.close()
            task = db.session.get(BackgroundIngestionTask, task_id)
            task.heroku_task_id = result.id
            db.session.add(task)
            db.session.commit()
            db.session.close()
        return {
            "status": 200,
            "message": f"Task {task.id} will be tried again in 45 seconds. Job id: {result.id}"
        }


@app.get("/assistants")
@login_required
def get_assistants():
    """Route to assistants

    Returns:
        render_template: assistants
    """
    assistants_limit = os.getenv("ASSISTANT_SLOTS", 5)
    traits_limit = os.getenv("TRAITS_SLOTS", 20)
    
    if current_user.is_authenticated:
        try:
            assistants = db.session.query(Assistant).filter(
                Assistant.user_id==current_user.id
            ).all()
            c_traits = [assistant.traits for assistant in assistants]
            assistants_with_traits = zip(assistants, c_traits)
            print(c_traits)
            traits = db.session.query(CharacterTrait).filter(
                CharacterTrait.user_id==current_user.id
            ).all()
        except OperationalError as e:
            db.session.rollback()
            db.session.close()
            return redirect(url_for('get_assistants'))
        assistants_available = int(assistants_limit) - len(assistants)
        traits_available = int(traits_limit) - len(traits)
        return render_template(
            "assistants.html",
            assistants_limit=assistants_limit,
            traits_limit=traits_limit,
            assistants_with_traits=assistants_with_traits,
            assistants_available=assistants_available,
            traits_available=traits_available,
            traits=traits      
        )
    else:
        return redirect(url_for("login"))


@app.post("/traits/create")
@login_required
def create_or_edit_trait():
    """Route to create a source

    Returns:
        redirect: assistants page
    """
    trait_name = request.form.get("trait")
    trait_value = request.form.get("value")
    trait_reason_why = request.form.get("reason-why")
    trait_id = request.form.get("trait-id", None)
    try:
        user_id = current_user.id
        if trait_id is not None:
            trait = db.session.get(CharacterTrait, trait_id)
            if trait.user_id == user_id:
                if int(trait_id) == trait.id and trait_name.lower() == trait.trait:
                    trait.value=trait_value,
                    trait.reason_why=trait_reason_why
                    db.session.add(trait)
                    db.session.commit()
                    flash(f"Succesfully edit trait {trait_id}")
                    db.session.close()
                    return redirect(url_for("get_assistants"))
            else:
                flash("Permission denied for not owned trait")
                return redirect(url_for("get_assistants"))

        else:
            trait = db.session.query(CharacterTrait).filter(
                CharacterTrait.user_id==user_id,
                CharacterTrait.trait==trait_name,
                CharacterTrait.value==trait_value
            ).first()
            print(trait_name, trait.trait)
            if trait is not None:
                flash("You already have the same trait with the same value")
                return redirect(url_for("get_assistants"))
            new_trait = CharacterTrait(
                trait=trait_name,
                value=trait_value,
                reason_why=trait_reason_why,
                user_id=user_id
            )
            db.session.add(new_trait)
            db.session.commit()
            trait_id = new_trait.id
            db.session.close()
            flash(f"Added trait {trait_id}")
            return redirect(url_for("get_assistants"))
    except OperationalError as e:
        flash(f"Operational error: {e} - Please retry...")
        db.session.rollback()
        db.session.close()
        return redirect(url_for('get_assistants'))
    except OperationalError as e:
        flash(f"Pending rollback: {e} - Please retry...")
        db.session.rollback()
        db.session.close()
        return redirect(url_for('get_assistants'))


@app.post("/traits/<trait_id>/delete")
@login_required
def delete_trait(trait_id):
    try:
        trait = db.session.get(CharacterTrait, trait_id)
        db.session.delete(trait)
        db.session.commit()
        db.session.close()
        flash("Trait deleted successfully")
        return redirect(url_for('get_assistants'))
    except OperationalError as e:
        flash(f"Operational error: {e} - Please retry...")
        db.session.rollback()
        db.session.close()
        return redirect(url_for('get_assistants'))
    except Exception as e:
        flash(f"Unknown error: {e} - Please retry...")
        db.session.rollback()
        db.session.close()
        return redirect(url_for('get_assistants'))


@app.post("/sources/<source_id>/delete")
@login_required
def delete_source(source_id):
    try:
        source = db.session.get(Source, source_id)
        db.session.delete(source)
        db.session.commit()
        db.session.close()
        flash("Source deleted successfully")
        return redirect(url_for('get_assistants'))
    except OperationalError as e:
        flash(f"Operational error: {e} - Please retry...")
        db.session.rollback()
        db.session.close()
        return redirect(url_for('get_assistants'))
    except Exception as e:
        flash(f"Unknown error: {e} - Please retry...")
        db.session.rollback()
        db.session.close()
        return redirect(url_for('get_assistants'))


@app.post("/collections/<collection_id>/delete")
@login_required
def delete_collection(collection_id):
    try:
        collection = db.session.get(Collection, collection_id)
        db.session.delete(collection)
        db.session.commit()
        db.session.close()
        flash("Collection deleted successfully")
        return redirect(url_for('get_assistants'))
    except OperationalError as e:
        flash(f"Operational error: {e} - Please retry...")
        db.session.rollback()
        db.session.close()
        return redirect(url_for('get_assistants'))
    except Exception as e:
        flash(f"Unknown error: {e} - Please retry...")
        db.session.rollback()
        db.session.close()
        return redirect(url_for('get_assistants'))


@app.post("/assistants/<assistant_id>/delete")
@login_required
def delete_assistant(assistant_id):
    try:
        assistant = db.session.get(Assistant, assistant_id)
        db.session.delete(assistant)
        db.session.commit()
        db.session.close()
        flash("Assistant deleted successfully")
        return redirect(url_for('get_assistants'))
    except OperationalError as e:
        flash(f"Operational error: {e} - Please retry...")
        db.session.rollback()
        db.session.close()
        return redirect(url_for('get_assistants'))
    except Exception as e:
        flash(f"Unknown error: {e} - Please retry...")
        db.session.rollback()
        db.session.close()
        return redirect(url_for('get_assistants'))


@app.get("/collections/<collection_id>")
@login_required
def get_collection(collection_id):
    print(collection_id)
    try:
        collection = db.session.get(Collection, int(collection_id))
        print(collection)
        if collection:
            if collection.user_id != current_user.id:
                db.session.close()
                return {
                    "status": 403,
                    "error": "Permission denied for not owned collection"
                }
            else:
                collection_name = collection.collection_name
                description = collection.documents_description
                sources = []
                for source in collection.sources:
                    sources.append({
                        "name": source.name,
                        "description":source.description
                    })
                db.session.close()
                return {
                    "status": 200,
                    "collection_name": collection_name,
                    "description": description,
                    "collection_id": collection_id,
                    "sources": sources
                    }
        else:
            return {"status": 404, "error": "No collection found"}
    except OperationalError as e:
        return {"status": 500, "error": f"Operational error: {e} - Please retry..."}
    except Exception as e:
        return {"status": 500, "error": f"Unknown error: {e} - Please retry..."}


@app.get("/assistants/<assistant_id>")
@login_required
def get_assistant(assistant_id):
    try:
        assistant = db.session.get(Assistant, int(assistant_id))
        print(assistant)
        if assistant:
            if assistant.user_id != current_user.id:
                db.session.close()
                return {
                    "status": 403,
                    "error": "Permission denied for not owned assistant"
                }
            else:
                assistant_name = assistant.name
                prompt = assistant.prompt
                traits = []
                for trait in assistant.traits:
                    traits.append({
                        "trait_id": trait.id,
                        "trait_name": trait.trait,
                        "trait_value": trait.value,
                        "trait_reason_why": trait.reason_why
                    })
                db.session.close()
                return {
                    "status": 200,
                    "assistant_id": assistant_id,
                    "assistant_name": assistant_name,
                    "prompt": prompt,
                    "traits": traits
                    }
        else:
            return {"status": 404, "error": "No collection found"}
    except OperationalError as e:
        return {"status": 500, "error": f"Operational error: {e} - Please retry..."}
    except Exception as e:
        return {"status": 500, "error": f"Unknown error: {e} - Please retry..."}
            