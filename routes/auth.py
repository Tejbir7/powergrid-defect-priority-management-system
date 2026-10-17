import random
from datetime import datetime, timedelta
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
    """Step 1 — collect email and send a 6-digit OTP."""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = db.session.scalar(db.select(User).where(User.email == email))

        if not user:
            # Don't reveal whether the email exists
            flash("If that email is registered, an OTP has been sent to it.", "info")
            return render_template("forgot_password.html")

        # Generate a 6-digit OTP and store it in the session (10-minute window)
        otp = str(random.randint(100000, 999999))
        session["pwd_reset_otp"] = otp
        session["pwd_reset_email"] = email
        session["pwd_reset_expiry"] = (datetime.now() + timedelta(minutes=10)).timestamp()

        smtp_user = current_app.config.get("MAIL_USERNAME")
        email_sent = False

        if smtp_user:
            try:
                msg = Message(
                    subject="PowerGrid — Password Reset OTP",
                    recipients=[user.email],
                    body=(
                        f"Hello {user.username},\n\n"
                        f"Your one-time password (OTP) for resetting your PowerGrid account is:\n\n"
                        f"    {otp}\n\n"
                        f"This OTP is valid for 10 minutes. "
                        f"If you did not request a password reset, you can safely ignore this email.\n\n"
                        f"— PowerGrid System"
                    ),
                    sender=smtp_user,
                )
                mail.send(msg)
                email_sent = True
                flash(f"A 6-digit OTP has been sent to {user.email}. Please check your inbox.", "success")
            except Exception as e:
                current_app.logger.error(f"Failed to send OTP email: {e}")
                flash("Email delivery failed. Use the OTP displayed below to continue.", "warning")

        session["pwd_reset_email_sent"] = email_sent

        if not email_sent:
            flash("SMTP is not configured. Use the OTP shown on the next page to continue.", "warning")

        return redirect(url_for("auth.verify_otp"))

    return render_template("forgot_password.html")


@auth_bp.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    """Step 2 — validate the OTP and redirect to the password-reset form."""
    if "pwd_reset_otp" not in session:
        flash("No active password reset session. Please start again.", "danger")
        return redirect(url_for("auth.forgot_password"))

    # Show OTP on-screen only when email could not be sent (demo / no-SMTP mode)
    email_sent = session.get("pwd_reset_email_sent", True)
    demo_otp = None if email_sent else session.get("pwd_reset_otp")

    if request.method == "POST":
        entered = request.form.get("otp", "").strip()

        # Check expiry
        if datetime.now().timestamp() > session.get("pwd_reset_expiry", 0):
            session.pop("pwd_reset_otp", None)
            session.pop("pwd_reset_email", None)
            session.pop("pwd_reset_expiry", None)
            session.pop("pwd_reset_email_sent", None)
            flash("OTP has expired. Please request a new one.", "danger")
            return redirect(url_for("auth.forgot_password"))

        if entered != session.get("pwd_reset_otp"):
            flash("Incorrect OTP. Please try again.", "danger")
            return render_template("verify_otp.html", demo_otp=demo_otp)

        # OTP is valid — resolve user and issue reset token
        email = session.get("pwd_reset_email")
        user = db.session.scalar(db.select(User).where(User.email == email))
        if not user:
            flash("User not found. Please start again.", "danger")
            return redirect(url_for("auth.forgot_password"))

        # Clear OTP data from session before issuing the token
        session.pop("pwd_reset_otp", None)
        session.pop("pwd_reset_email", None)
        session.pop("pwd_reset_expiry", None)
        session.pop("pwd_reset_email_sent", None)

        cipher = get_cipher()
        token = cipher.encrypt(f"{user.id}:{int(datetime.now().timestamp())}".encode()).decode()

        flash("OTP verified. Please set your new password.", "success")
        return redirect(url_for("auth.reset_password", token=token))

    return render_template("verify_otp.html", demo_otp=demo_otp)


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
