"""Account pages."""
import hmac
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from starlette.background import BackgroundTask

from .. import auth, backup, db, display, handmade, importer, scans
from .. import region, fx
from .. import web
from ..web import (_Route, _account_context, _account_page, _sign_in, admin_user, conn, current_user, owner_user, setup_code,
                   templates)

router = APIRouter(route_class=_Route)


def _wait_text(wait: float, what: str = "passwords") -> str:
    minutes = max(round(wait / 60), 1)
    return f"Too many wrong {what}. Try again in {minutes} minute{'s' if minutes != 1 else ''}."


@router.get("/setup")
def setup_form(request: Request):
    if auth.user_count(conn) > 0:
        return RedirectResponse("/login", status_code=303)
    setup_code()  # made (logged, and written to its file) if it isn't yet
    return templates.TemplateResponse(request, "login.html", {"setup": True})


@router.post("/setup")
def setup(request: Request, username: str = Form(...), password: str = Form(...), code: str = Form("")):
    # Only the very first account can be created here; it becomes the admin. With the setup code
    # from the server's log, and wrong codes count towards the same per-address limit as wrong passwords.
    if auth.user_count(conn) > 0:
        raise HTTPException(403)
    address = request.client.host if request.client else ""
    wait = web.lockout.locked_for("(setup)", address)
    if wait:
        return templates.TemplateResponse(request, "login.html", {"setup": True, "error": _wait_text(wait, "codes")}, status_code=429)
    expected = setup_code() or ""
    if not hmac.compare_digest(code.strip().upper().replace(" ", "").encode(), expected.encode()):
        web.lockout.failed("(setup)", address)
        return templates.TemplateResponse(request, "login.html", {"setup": True, "error": "That setup code isn't right. "
                                          "It's in the server's log (docker logs dexstash) and in setup-code.txt in the data folder."}, status_code=400)
    try:
        user_id = auth.create_user(conn, username, password, is_admin=True)
    except ValueError as e:
        return templates.TemplateResponse(request, "login.html", {"setup": True, "error": str(e)}, status_code=400)
    setup_code()  # the code and its file go
    _sign_in(request, user_id)
    return RedirectResponse("/", status_code=303)


@router.get("/login")
def login_form(request: Request, restored: str = ""):
    notice = None
    if backup.find(web.BACKUPS, restored):
        notice = f"Backup restored. Log in again. What was there before is saved as {restored}."
    return templates.TemplateResponse(request, "login.html", {"setup": False, "notice": notice})


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    # Wrong passwords lock out this username from this address only, so nobody else can lock you out.
    address = request.client.host if request.client else ""
    wait = web.lockout.locked_for(username, address)
    if wait:
        return templates.TemplateResponse(request, "login.html", {"setup": False, "error": _wait_text(wait)}, status_code=429)
    user = auth.authenticate(conn, username, password)
    if user is None:
        web.lockout.failed(username, address)
        return templates.TemplateResponse(
            request, "login.html", {"setup": False, "error": "Wrong username or password."}, status_code=401
        )
    web.lockout.succeeded(username, address)
    _sign_in(request, user["id"])
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.post("/logout/everywhere")
def logout_everywhere(request: Request, user=Depends(current_user)):
    """Account › Sign out everywhere: a new session key signs out every phone and computer, this one too."""
    auth.new_session_key(conn, user["id"])
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/account")
def account_view(request: Request, user=Depends(current_user)):
    return templates.TemplateResponse(request, "account.html", _account_context(request, user))


@router.post("/account/password")
def account_password(
    request: Request, current: str = Form(...), new: str = Form(...), again: str = Form(...),
    user=Depends(current_user),
):
    if not auth.verify_password(current, user["password_hash"]):
        return _account_page(request, user, "Your current password isn't right.", error=True)
    if new != again:
        return _account_page(request, user, "The new passwords don't match.", error=True)
    try:
        request.session["key"] = auth.set_password(conn, user["id"], new)
    except ValueError as e:
        return _account_page(request, user, str(e), error=True)
    return _account_page(request, user, "Password changed. Any other device you were signed in on is signed out.")


@router.post("/account/users")
def account_add_user(
    request: Request, username: str = Form(...), password: str = Form(...), is_admin: str = Form(""),
    user=Depends(admin_user),
):
    try:
        auth.create_user(conn, username, password, is_admin=bool(is_admin), collection_id=user["collection_id"])
    except ValueError as e:
        return _account_page(request, user, str(e), error=True)
    return _account_page(request, user, f"Added {username.strip()}. They share this collection.")


def _member(user, user_id: int) -> dict:
    row = conn.execute("SELECT * FROM users WHERE id = ? AND collection_id = ?",
                       (user_id, user["collection_id"])).fetchone()
    if row is None:
        raise HTTPException(404)
    return dict(row)


@router.post("/account/users/{user_id}/password")
def account_reset_password(request: Request, user_id: int, password: str = Form(...), user=Depends(admin_user)):
    member = _member(user, user_id)
    try:
        key = auth.set_password(conn, user_id, password)
    except ValueError as e:
        return _account_page(request, user, str(e), error=True)
    if user_id == user["id"]:
        request.session["key"] = key
    return _account_page(request, user, f"New password set for {member['username']}. They're signed out everywhere.")


@router.post("/account/users/{user_id}/admin")
def account_set_admin(request: Request, user_id: int, is_admin: str = Form(""), user=Depends(admin_user)):
    member = _member(user, user_id)
    try:
        auth.set_admin(conn, user, user_id, bool(is_admin))
    except ValueError as e:
        return _account_page(request, user, str(e), error=True)
    return _account_page(request, user, f"{member['username']} is {'now' if is_admin else 'no longer'} an admin.")


@router.post("/account/users/{user_id}/remove")
def account_remove_user(request: Request, user_id: int, user=Depends(admin_user)):
    member = _member(user, user_id)
    try:
        auth.remove_user(conn, user, user_id)
    except ValueError as e:
        return _account_page(request, user, str(e), error=True)
    return _account_page(request, user, f"Removed {member['username']}. What they added stays in the collection.")


@router.post("/account/clear")
def account_clear(request: Request, confirm: str = Form(""), forget_matches: str = Form(""), user=Depends(admin_user)):
    """The second confirmation is typing CLEAR (the first is the browser's confirm box)."""
    if confirm.strip() != "CLEAR":
        return _account_page(request, user, "Nothing was cleared. Type CLEAR in capitals to confirm.", error=True)
    importer.clear_collection(conn, user["collection_id"], forget_matches=bool(forget_matches))
    scans.clear_collection(conn, user["collection_id"])
    return _account_page(request, user, "The collection is empty. Accounts and card data are still here.")



@router.post("/account/backups")
def backup_make(request: Request, user=Depends(owner_user)):
    path = backup.make(conn, web.BACKUPS, "manual")
    return _account_page(request, user, f"Backed up to {path.name}.")


@router.get("/account/photos.zip")
def photos_download(user=Depends(owner_user)):
    """Your own card and chase photos, which the database backups don't hold."""
    made = backup.photos_zip(handmade.FOLDER)
    if made is None:
        return RedirectResponse("/account?photos=none#backups", status_code=303)
    name = f"dexstash-photos-{display.today().isoformat()}.zip"
    return FileResponse(made, filename=name, media_type="application/zip",
                        background=BackgroundTask(made.unlink, missing_ok=True))


@router.get("/account/backups/{name}")
def backup_download(name: str, user=Depends(owner_user)):
    path = backup.find(web.BACKUPS, name)
    if path is None:
        raise HTTPException(404, "No such backup.")
    return FileResponse(path, filename=name, media_type="application/vnd.sqlite3")


@router.post("/account/backups/{name}/restore")
def backup_restore(request: Request, name: str, confirm: str = Form(""), user=Depends(owner_user)):
    if confirm.strip() != "RESTORE":
        return _account_page(request, user, "Nothing was restored. Type RESTORE in capitals to confirm.", error=True)
    try:
        safety = backup.restore(conn, web.BACKUPS, name)
    except ValueError as e:
        return _account_page(request, user, str(e), error=True)
    db.init(conn)  # an older backup needs its tables brought up to date
    backup.mark_version(conn)
    region.load(conn)
    fx.load(conn)
    request.session.clear()  # accounts are as they were in the backup
    return RedirectResponse(f"/login?restored={quote(safety.name)}", status_code=303)


@router.post("/account/backups/{name}/delete")
def backup_delete(name: str, user=Depends(owner_user)):
    backup.delete(web.BACKUPS, name)
    return RedirectResponse("/account#backups", status_code=303)
