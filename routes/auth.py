from datetime import datetime
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from flask_mail import Message
from extensions import db, mail
from models.user import User
from models.defect import Defect
from services.crypto import get_cipher

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    from models.workspace import Workspace, WorkspaceMember

    user_count = db.session.scalar(db.select(db.func.count()).select_from(User)) or 0
    is_first_user = user_count == 0

    join_code = request.args.get("join") or request.form.get("join") or session.get("pending_invite_code")
    joining_workspace = None
    if join_code:
        joining_workspace = db.session.scalar(db.select(Workspace).where(Workspace.invite_code == join_code))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        errors = []
        if not username:
            errors.append("Username is required.")
        if not email:
            errors.append("Email address is required.")
        if len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if password != confirm:
            errors.append("Passwords do not match.")
        if db.session.scalar(db.select(User).where(User.email == email)):
            errors.append("Email is already registered. Please sign in or reset your password.")
        if db.session.scalar(db.select(User).where(User.username == username)):
            errors.append("Username is already taken. Please choose another username.")

        if errors:
            for err in errors:
                flash(err, "danger")
            return render_template(
                "register.html",
                is_first_user=is_first_user,
                join_code=join_code,
                joining_workspace=joining_workspace,
            )

        user = User(
            email=email,
            username=username,
            role="head" if is_first_user else "engineer",
            is_approved=True,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        login_user(user)

        # Process join code if present
        if joining_workspace:
            membership = WorkspaceMember(
                workspace_id=joining_workspace.id,
                user_id=user.id,
                role="engineer",
                is_approved=True,
            )
            db.session.add(membership)
            db.session.commit()
            session["active_workspace_id"] = joining_workspace.id
            session.pop("pending_invite_code", None)
            flash(f"Account created! Welcome to {joining_workspace.name}.", "success")
            return redirect(url_for("dashboard"))

        flash("Account created successfully! Welcome to PowerGrid Central.", "success")
        return redirect(url_for("workspace.workspaces_hub"))

    return render_template(
        "register.html",
        is_first_user=is_first_user,
        join_code=join_code,
        joining_workspace=joining_workspace,
    )


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    from models.workspace import Workspace, WorkspaceMember

    user_count = db.session.scalar(db.select(db.func.count()).select_from(User))
    if not user_count:
        return redirect(url_for("register"))

    join_code = request.args.get("join") or request.form.get("join") or session.get("pending_invite_code")
    joining_workspace = None
    if join_code:
        joining_workspace = db.session.scalar(db.select(Workspace).where(Workspace.invite_code == join_code))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = db.session.scalar(db.select(User).where(User.email == email))

        if not user or not user.check_password(password):
            flash("Invalid email or password.", "danger")
            return render_template(
                "login.html",
                join_code=join_code,
                joining_workspace=joining_workspace,
            )

        # Ensure approved user account
        if not user.is_approved:
            user.is_approved = True
            db.session.commit()

        login_user(user, remember=request.form.get("remember") == "on")

        # Process join code if present
        if joining_workspace:
            mem = db.session.scalar(
                db.select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == joining_workspace.id,
                    WorkspaceMember.user_id == user.id,
                )
            )
            if not mem:
                mem = WorkspaceMember(
                    workspace_id=joining_workspace.id,
                    user_id=user.id,
                    role="engineer",
                    is_approved=True,
                )
                db.session.add(mem)
                db.session.commit()
            session["active_workspace_id"] = joining_workspace.id
            session.pop("pending_invite_code", None)
            flash(f"Welcome back! You have accessed {joining_workspace.name}.", "success")
            return redirect(url_for("dashboard"))

        # In-app reminder banner
        pending_count = (
            db.session.scalar(
                db.select(db.func.count()).select_from(Defect).where(
                    Defect.assigned_user_id == user.id,
                    Defect.status != "Resolved",
                )
            )
            or 0
        )
        if pending_count:
            flash(
                f"You have {pending_count} pending defect(s) assigned to you.",
                "reminder",
            )

        next_page = request.args.get("next")
        if next_page and next_page.startswith("/") and not next_page.startswith("//") and next_page != "/login":
            return redirect(next_page)

        # Standard post-login landing: Workspaces Hub
        return redirect(url_for("workspace.workspaces_hub"))

    return render_template(
        "login.html",
        join_code=join_code,
        joining_workspace=joining_workspace,
    )


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@auth_bp.route("/pending-approval")
@login_required
def pending_approval():
    if current_user.is_approved:
        return redirect(url_for("dashboard"))
    return render_template("pending_approval.html")


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Handle password reset requests."""
    reset_url = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = db.session.scalar(db.select(User).where(User.email == email))

        if not user:
            # For security, avoid disclosing user existence, or give clean notice
            flash("If that email is registered, password recovery instructions have been prepared.", "info")
            return render_template("forgot_password.html", reset_url=None)

        cipher = get_cipher()
        token = cipher.encrypt(f"{user.id}:{int(datetime.now().timestamp())}".encode()).decode()
        reset_url = url_for("auth.reset_password", token=token, _external=True)

        smtp_user = current_app.config.get("MAIL_USERNAME")
        if smtp_user:
            try:
                msg = Message(
                    subject="PowerGrid — Password Reset Request",
                    recipients=[user.email],
                    body=(
                        f"Hello {user.username},\n\n"
                        f"We received a request to reset your PowerGrid password.\n"
                        f"Please click or paste the link below to set a new password:\n\n"
                        f"{reset_url}\n\n"
                        f"This link expires in 1 hour. If you did not request this, please ignore this email.\n\n"
                        f"— PowerGrid System"
                    ),
                    sender=smtp_user,
                )
                mail.send(msg)
                flash(f"Password reset instructions have been sent to {user.email}.", "success")
                return redirect(url_for("login"))
            except Exception as e:
                current_app.logger.error(f"Failed to send reset email: {e}")
                # Fallback to direct reset link when SMTP fails
                flash("SMTP email delivery encountered an error. A secure recovery link has been generated below.", "warning")
        else:
            # Standalone / demo environment without SMTP configured
            flash("Password recovery link generated. Click below to reset your password.", "success")

    return render_template("forgot_password.html", reset_url=reset_url)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    """Reset user password with valid cryptographic token."""
    cipher = get_cipher()
    try:
        data = cipher.decrypt(token.encode(), ttl=3600).decode()
        user_id_str, _ = data.split(":", 1)
        user = db.session.get(User, int(user_id_str))
    except Exception:
        user = None

    if not user:
        flash("Password reset link is invalid or has expired. Please request a new one.", "danger")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        errors = []
        if len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if password != confirm:
            errors.append("Passwords do not match.")

        if errors:
            for err in errors:
                flash(err, "danger")
            return render_template("reset_password.html", token=token, user=user)

        user.set_password(password)
        db.session.commit()
        flash("Your password has been successfully reset. You can now sign in with your new password.", "success")
        return redirect(url_for("login"))

    return render_template("reset_password.html", token=token, user=user)
