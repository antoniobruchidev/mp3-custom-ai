import os
from flask import flash, redirect, render_template, request, url_for
from celery.result import AsyncResult
from custom_assistant import app, db, argon2
from custom_assistant.inference import chat
from custom_assistant.mail import forgot_password_email, send_activation_email
from custom_assistant.models import User
from custom_assistant.tasks import celery, add
from flask_login import LoginManager, current_user, login_required, login_user, logout_user


login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)

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
    prompt = request.form.get("base-prompt")
    traits = request.form.get("traits")
    message= request.form.get("message")
    answer, prompt_tokens, comp_tokens = chat(prompt, message, traits)
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
    return render_template("playground.html")


@app.get("/collections")
def collections():
    """Route to collections

    Returns:
        render_template: collections
    """
    return render_template("collections.html")


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
        user = User(email=email).sign_up_with_email(
            request.form.get("password", None),
            request.form.get("confirm-password", None)
        )
        if user is not None:
            db.session.add(user)
            db.session.commit()
            send_activation_email(user)
            return redirect(url_for("login", g_client_id=g_client_id))
        else:
            error = f"Email {user.email} already present"
            return redirect(url_for(
                "login",
                error=error,
                g_client_id=g_client_id
            ))
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
    user = db.session.get(User, user_id)
    user.verified = True
    db.session.add(user)
    db.session.commit()
    return redirect(url_for("login"))
    

@app.route("/login", methods=['GET', 'POST'])
def login():
    """Route to login

    Returns:
        render_template: login page if method is get
        json: if post
    """
    g_client_id = os.getenv("GOOGLE_CLIENT_ID")
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
    

@app.get("/forgot_password/<email>")
def forgot_password(email):
    """Route to request a change of password via email

    Args:
        email (str): the user email address

    Returns:
        json: a message or an error
    """
    user = db.session.query(User).filter(User.email==email).first()
    if user is not None:
        user.forgot_passwd_url = forgot_password_email(user)
        db.session.add(user)
        db.session.commit()
        return {
            "status": 200,
            "message": "An email has been sent to you to change your password."}
    else:
        return {"status": 404, "error": "Email not found"}


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
    user = db.session.query(User).filter(User.forgot_passwd_url==hash).first()
    if user is None:
        message = "No users password change at this url"
        return render_template(
            "login.html",
            error=message,
            g_client_id=g_client_id
        )
    else:    
        if request.method == "POST":
            user.password = argon2.generate_password_hash(
                request.form.get("password")
            )
            db.session.add(user)
            db.session.commit()
            message = "Password changed correctly"
            return render_template(
                "login.html",
                g_client_id=g_client_id,
                error=message
            )
        else:
            return render_template(
                "forgot_password.html", hash=hash, user=user
            )
        
        
@login_required
@app.route("/logout")
def logout():
    """Route to logout

    Returns:
        redirect: home
    """
    logout_user()
    return redirect(url_for('home'))
