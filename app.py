# app.py
import streamlit as st
import sqlite3
import hashlib
import os
import shutil
import stat
import html
from datetime import datetime
from pathlib import Path

# ---------------- Config ----------------
st.set_page_config(page_title="Smart Class Companion", layout="wide")
DATA_DIR = Path("submissions")
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = "smartclass.db"

# ---------------- Styles (old visual, blue login box + minimal pro theme) ----------------
st.markdown("""
<style>
/* page visuals */
.banner { background: linear-gradient(135deg,#143a7b,#1E3A8A); color: white; padding: 20px; border-radius: 12px; }
.card { background: #ffffff; border-radius:10px; padding:14px; box-shadow:0 6px 18px rgba(0,0,0,0.06); margin-top:12px; }
.center-box { max-width:620px; margin: 20px auto; }
.login-box { background: linear-gradient(180deg,#0f3b77,#14509c); color: white; padding:22px; border-radius:10px; }
.input-white input { background:white !important; color:black !important; }
.ann-highlight { background:#fff8dc; border-left:6px solid #f59e0b; padding:16px; border-radius:8px; }
.goal-highlight { background:#ecf8ff; border-left:6px solid #0ea5e9; padding:16px; border-radius:8px; }
.ann-small { color:#6b7280; font-size:13px; }
.status-pill { padding:5px 10px; border-radius:999px; font-weight:600; font-size:12px; }
.pending { background:#fff7ed; color:#c2410c; }
.accepted { background:#ecfdf5; color:#065f46; }
.needs { background:#fef2f2; color:#991b1b; }

/* chat styles */
.chat-left { background:#f3f4f6; padding:10px 12px; border-radius:12px; display:block; margin-bottom:8px; max-width:78%; }
.chat-right { background:#e6f0ff; padding:10px 12px; border-radius:12px; display:block; margin-bottom:8px; max-width:78%; margin-left:auto; }
.reply-indent { margin-left:22px; padding:8px; border-left:3px solid #e5e7eb; border-radius:6px; background:#fbfbfb; margin-bottom:6px; }
.small-muted { color:#6b7280; font-size:12px; }
.button-small { padding:6px 10px; font-size:13px; border-radius:8px; }
</style>
""", unsafe_allow_html=True)

# ---------------- DB Helpers & Migration ----------------
def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur = conn.cursor()

    # create core tables (safe)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            role TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT,
            posted_by TEXT,
            role TEXT,
            ts TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            goal TEXT,
            ts TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            folder TEXT,
            filename TEXT,
            uploader TEXT,
            ts TEXT,
            review_status TEXT DEFAULT 'Pending'
        )
    """)
    # chat table (we will ensure parent_id exists via migration)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            message TEXT,
            ts TEXT
            -- parent_id column may be added below if missing
        )
    """)
    conn.commit()

    # migration: add parent_id if not present
    cur.execute("PRAGMA table_info(chat)")
    cols = [r[1] for r in cur.fetchall()]
    if "parent_id" not in cols:
        try:
            cur.execute("ALTER TABLE chat ADD COLUMN parent_id INTEGER")
            conn.commit()
        except Exception:
            # on some platforms ALTER TABLE ADD COLUMN can still throw; ignore to keep app usable
            pass

    return conn, cur

conn, cur = init_db()

# ---------------- Utilities ----------------
def hash_pw(p: str) -> str:
    return hashlib.sha256(p.encode()).hexdigest()

# ---------------- User management ----------------
def add_user_db(username: str, password: str, role: str) -> bool:
    username = username.strip()
    if username == "":
        return False
    # prevent case-insensitive duplicates
    cur.execute("SELECT id FROM users WHERE lower(username)=lower(?)", (username,))
    if cur.fetchone():
        return False
    try:
        cur.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                    (username, hash_pw(password), role))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def authenticate_db(username: str, password: str):
    username = username.strip()
    if username == "":
        return None
    cur.execute("SELECT id, username, role, password FROM users WHERE lower(username)=lower(?)", (username,))
    row = cur.fetchone()
    if not row:
        return None
    uid, stored_username, role, stored_pw = row
    input_hashed = hash_pw(password)
    if input_hashed == stored_pw:
        return (uid, stored_username, role)
    # support legacy plain-text password (migrate to hash)
    if password == stored_pw:
        try:
            cur.execute("UPDATE users SET password=? WHERE id=?", (input_hashed, uid))
            conn.commit()
        except Exception:
            pass
        return (uid, stored_username, role)
    return None

# ---------------- Announcements ----------------
def add_announcement_db(message: str, posted_by: str, role: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    cur.execute("INSERT INTO announcements (message, posted_by, role, ts) VALUES (?,?,?,?)",
                (message.strip(), posted_by, role, ts))
    conn.commit()

def fetch_announcements_db(search: str = ""):
    if search.strip():
        s = f"%{search.strip().lower()}%"
        cur.execute("""SELECT id, message, posted_by, role, ts FROM announcements 
                       WHERE lower(message) LIKE ? OR lower(posted_by) LIKE ? OR lower(role) LIKE ?
                       ORDER BY id DESC""", (s, s, s))
    else:
        cur.execute("SELECT id, message, posted_by, role, ts FROM announcements ORDER BY id DESC")
    return cur.fetchall()

def delete_announcement_db(aid: int):
    cur.execute("DELETE FROM announcements WHERE id=?", (aid,))
    conn.commit()

# ---------------- Goals ----------------
def add_goal_db(username: str, goal: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    cur.execute("INSERT INTO goals (username, goal, ts) VALUES (?, ?, ?)", (username, goal.strip(), ts))
    conn.commit()

def fetch_goals_db(username: str = None):
    if username:
        cur.execute("SELECT id, goal, ts FROM goals WHERE username=? ORDER BY id DESC", (username,))
    else:
        cur.execute("SELECT id, goal, ts, username FROM goals ORDER BY id DESC")
    return cur.fetchall()

def delete_goal_db(gid: int, username: str):
    cur.execute("DELETE FROM goals WHERE id=? AND username=?", (gid, username))
    conn.commit()

# ---------------- Notes / Submissions ----------------
def ensure_folder(folder: str):
    (DATA_DIR / folder).mkdir(parents=True, exist_ok=True)

def list_folders():
    return sorted([d.name for d in DATA_DIR.iterdir() if d.is_dir()])

def save_upload_db(folder: str, uploaded_file, uploader: str):
    ensure_folder(folder)
    dest = DATA_DIR / folder / uploaded_file.name
    # if file exists, append timestamp to avoid overwrite
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        new_name = f"{stem}_{int(datetime.now().timestamp())}{suffix}"
        dest = DATA_DIR / folder / new_name
    with open(dest, "wb") as f:
        f.write(uploaded_file.getbuffer())
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    cur.execute("INSERT INTO notes (folder, filename, uploader, ts) VALUES (?,?,?,?)",
                (folder, dest.name, uploader, ts))
    conn.commit()

def fetch_files_db(folder: str, search: str = ""):
    if search.strip():
        s = f"%{search.strip().lower()}%"
        cur.execute("""SELECT id, filename, uploader, ts, review_status FROM notes
                       WHERE folder=? AND (lower(filename) LIKE ? OR lower(uploader) LIKE ? OR lower(review_status) LIKE ?)
                       ORDER BY id DESC""", (folder, s, s, s))
    else:
        cur.execute("""SELECT id, filename, uploader, ts, review_status FROM notes
                       WHERE folder=? ORDER BY id DESC""", (folder,))
    return cur.fetchall()

def set_review_status_db(note_id: int, status: str):
    cur.execute("UPDATE notes SET review_status=? WHERE id=?", (status, note_id))
    conn.commit()

def delete_note_record_and_file(note_id: int):
    cur.execute("SELECT folder, filename FROM notes WHERE id=?", (note_id,))
    row = cur.fetchone()
    if row:
        folder, filename = row
        path = DATA_DIR / folder / filename
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass
    cur.execute("DELETE FROM notes WHERE id=?", (note_id,))
    conn.commit()

# helper to remove read-only files on Windows
def handle_remove_readonly(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE)
    except Exception:
        pass
    try:
        func(path)
    except Exception:
        pass

def delete_folder_and_contents(folder: str):
    folder_path = DATA_DIR / folder
    if folder_path.exists() and folder_path.is_dir():
        # delete DB records for that folder
        try:
            cur.execute("DELETE FROM notes WHERE folder=?", (folder,))
            conn.commit()
        except Exception:
            pass
        # remove folder tree (forcefully handle read-only)
        try:
            shutil.rmtree(folder_path, ignore_errors=False, onerror=handle_remove_readonly)
        except Exception:
            # fallback: try to remove files individually then rmdir
            try:
                for f in folder_path.iterdir():
                    try:
                        if f.is_file():
                            os.chmod(f, stat.S_IWRITE)
                            f.unlink()
                    except Exception:
                        pass
                folder_path.rmdir()
            except Exception:
                pass

# ---------------- Chat (with one-level replies) ----------------
def add_chat_message_db(username: str, message: str, parent_id: int = None):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    # parent_id may be None
    cur.execute("INSERT INTO chat (username, message, ts, parent_id) VALUES (?,?,?,?)", (username, message.strip(), ts, parent_id))
    conn.commit()

def fetch_chat_db(search: str = ""):
    """
    Fetch top-level messages (parent_id IS NULL) in ASC order (old -> new).
    """
    if search.strip():
        s = f"%{search.strip().lower()}%"
        cur.execute("""SELECT id, username, message, ts FROM chat
                       WHERE parent_id IS NULL AND (lower(username) LIKE ? OR lower(message) LIKE ?)
                       ORDER BY id ASC""", (s, s))
    else:
        cur.execute("SELECT id, username, message, ts FROM chat WHERE parent_id IS NULL ORDER BY id ASC")
    return cur.fetchall()

def fetch_replies(parent_msg_id: int):
    cur.execute("SELECT id, username, message, ts FROM chat WHERE parent_id=? ORDER BY id ASC", (parent_msg_id,))
    return cur.fetchall()

def delete_chat_message_db(mid: int):
    # delete the message and its replies
    cur.execute("DELETE FROM chat WHERE id=? OR parent_id=?", (mid, mid))
    conn.commit()

def delete_chat_reply_db(rid: int):
    cur.execute("DELETE FROM chat WHERE id=?", (rid,))
    conn.commit()

def clear_chat_db():
    cur.execute("DELETE FROM chat")
    conn.commit()

# ---------------- Session ----------------
if "auth" not in st.session_state:
    st.session_state.auth = None  # dict: {username, role}
if "reply_inputs" not in st.session_state:
    st.session_state.reply_inputs = {}  # keyed by top-level message id as string

# ---------------- Login / Register UI (centered blue box style) ----------------
def login_register_ui():
    st.markdown('<div class="center-box">', unsafe_allow_html=True)
    st.markdown('<div class="login-box">', unsafe_allow_html=True)
    st.markdown("<h2 style='margin:6px 0;'>🔐 Smart Class Companion</h2>", unsafe_allow_html=True)
    tabs = st.tabs(["Login", "Register"])
    with tabs[0]:
        login_user = st.text_input("Username", key="ui_login_user")
        login_pass = st.text_input("Password", type="password", key="ui_login_pass")
        if st.button("Login"):
            rec = authenticate_db(login_user, login_pass)
            if rec:
                st.session_state.auth = {"username": rec[1], "role": rec[2]}
                st.success(f"Welcome {rec[1]} ({rec[2]})")
                st.rerun()
            else:
                st.error("Invalid username or password")
    with tabs[1]:
        reg_user = st.text_input("Choose username", key="ui_reg_user")
        reg_pass = st.text_input("Choose password", type="password", key="ui_reg_pass")
        reg_role = st.selectbox("Role", ["Student", "CR", "Tutor"], key="ui_reg_role")
        if st.button("Register"):
            if reg_user.strip() and reg_pass.strip():
                ok = add_user_db(reg_user, reg_pass, reg_role)
                if ok:
                    st.success("Account created. Please login.")
                else:
                    st.error("Username already exists.")
            else:
                st.warning("Enter username & password.")
    st.markdown('</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

# ---------------- Main App ----------------
if st.session_state.auth is None:
    login_register_ui()
    st.stop()

username = st.session_state.auth["username"]
role = st.session_state.auth["role"]

with st.sidebar:
    st.markdown(f"Logged in: {html.escape(username)} ({html.escape(role)})")
    if st.button("Logout"):
        st.session_state.auth = None
        st.rerun()
    st.markdown("---")
    menu_choice = st.radio("Menu", ["🏠 Home", "📢 Announcements", "🎯 Goals", "📂 Submissions", "💬 Chatbox"])

# ---------------- HOME ----------------
if menu_choice == "🏠 Home":
    st.title("🎓 Smart Class Companion")
    st.markdown('<div class="banner"><h2>Welcome, Classmates! 👋</h2><p>Stay connected with announcements, goals, submissions, and chat.</p></div>', unsafe_allow_html=True)

    # Announcement highlight (most recent)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("🔔 Highlight")
    anns = fetch_announcements_db()
    if anns:
        aid, msg, by_user, arole, ts = anns[0]
        st.markdown(f"<div class='ann-highlight'><h3>{html.escape(msg)}</h3><p class='ann-small'>By {html.escape(by_user)} ({html.escape(arole)}) • {ts}</p></div>", unsafe_allow_html=True)
        if len(anns) > 1:
            st.markdown("<hr>", unsafe_allow_html=True)
            for row in anns[1:3]:
                _, m, b, r, t = row
                st.markdown(f"{html.escape(m)}**  \n<span class='ann-small'>By {html.escape(b)} ({html.escape(r)}) • {t}</span>", unsafe_allow_html=True)
    else:
        st.info("No announcements yet.")
    st.markdown('</div>', unsafe_allow_html=True)

    # Goal highlight (most recent of this user)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("🎯 Highlighted Goal (yours)")
    my_goals = fetch_goals_db(username)
    if my_goals:
        gid, gtext, gts = my_goals[0]
        st.markdown(f"<div class='goal-highlight'><h3>{html.escape(gtext)}</h3><p class='ann-small'>Added on {gts}</p></div>", unsafe_allow_html=True)
        if len(my_goals) > 1:
            st.markdown("<hr>", unsafe_allow_html=True)
            for g in my_goals[1:3]:
                _, gt, gts2 = g
                st.markdown(f"{html.escape(gt)}**  \n<span class='ann-small'>{gts2}</span>", unsafe_allow_html=True)
    else:
        st.info("You have no saved goals.")
    st.markdown('</div>', unsafe_allow_html=True)

# ---------------- ANNOUNCEMENTS ----------------
elif menu_choice == "📢 Announcements":
    st.header("📢 Class Announcements")
    # Post (CR/Tutor)
    if role in ("CR", "Tutor"):
        with st.expander("➕ Post a new announcement", expanded=True):
            new_msg = st.text_area("Announcement text", key="new_ann")
            if st.button("Post Announcement"):
                if new_msg.strip():
                    add_announcement_db(new_msg, username, role)
                    st.success("Announcement posted.")
                    st.rerun()
                else:
                    st.warning("Please type something.")

    # Search and display
    search = st.text_input("Search announcements (message/poster/role)", key="search_ann")
    rows = fetch_announcements_db(search=search)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    if not rows:
        st.info("No announcements.")
    else:
        for aid, msg, by_user, arole, ts in rows:
            cols = st.columns([8,2])
            with cols[0]:
                st.markdown(f"{html.escape(msg)}**  \n<span class='ann-small'>By {html.escape(by_user)} ({html.escape(arole)}) • {ts}</span>", unsafe_allow_html=True)
            with cols[1]:
                if role in ("CR", "Tutor"):
                    if st.button("Delete", key=f"del_ann_{aid}"):
                        delete_announcement_db(aid)
                        st.success("Deleted.")
                        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

# ---------------- GOALS ----------------
elif menu_choice == "🎯 Goals":
    st.header("🎯 Goal Setter")
    with st.expander("➕ Add a goal", expanded=True):
        new_goal = st.text_input("Enter your goal", key="goal_input")
        if st.button("Save Goal"):
            if new_goal.strip():
                add_goal_db(username, new_goal)
                st.success("Goal saved.")
                st.rerun()
            else:
                st.warning("Please enter a goal.")

    st.markdown('<div class="card">', unsafe_allow_html=True)
    my_goals = fetch_goals_db(username)
    if not my_goals:
        st.info("No goals yet.")
    else:
        for gid, gtext, gts in my_goals:
            c1, c2 = st.columns([8,2])
            with c1:
                st.markdown(f"✅ {html.escape(gtext)}  \n<span class='ann-small'>{gts}</span>", unsafe_allow_html=True)
            with c2:
                if st.button("Delete", key=f"del_goal_{gid}"):
                    delete_goal_db(gid, username)
                    st.success("Deleted.")
                    st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

# ---------------- SUBMISSIONS ----------------
elif menu_choice == "📂 Submissions":
    st.header("📂 Submissions (folders for assignments / resources)")

    # Folder management
    with st.expander("📁 Manage Folders", expanded=True):
        if role in ("CR", "Tutor"):
            nf = st.text_input("Create a new folder (e.g., Assignment1)", key="new_folder")
            if st.button("Create Folder"):
                if nf.strip():
                    ensure_folder(nf)
                    st.success(f"Folder '{nf}' created.")
                    st.rerun()
                else:
                    st.warning("Enter a folder name.")

        folders = list_folders()
        if not folders:
            st.info("No folders yet. Ask CR/Tutor to create one.")
        else:
            # Provide delete folder option for CR/Tutor:
            if role in ("CR", "Tutor"):
                sel_del = st.selectbox("Folder to delete", [""] + folders, key="sel_del")
                if sel_del:
                    # one-click delete (as requested) - immediate deletion
                    if st.button("Delete Folder"):
                        delete_folder_and_contents(sel_del)
                        st.success(f"Folder '{sel_del}' deleted (files removed).")
                        st.rerun()

    # Choose a folder to view/upload
    folders = list_folders()
    if folders:
        sel = st.selectbox("Choose folder", folders, key="sel_folder")
        fsearch = st.text_input("Search files (filename/uploader/status)", key=f"file_search_{sel}")
        up = st.file_uploader("Upload file to selected folder", key=f"up_{sel}",
                              type=["pdf", "docx", "pptx", "png", "jpg", "jpeg", "zip"])
        if up is not None:
            save_upload_db(sel, up, username)
            st.success(f"Uploaded {up.name} to {sel}")
            st.rerun()

        files = fetch_files_db(sel, search=fsearch)
        if not files:
            st.info("No files in this folder.")
        else:
            for fid, fname, uploader, ts, status in files:
                pill_cls = "pending"
                if status == "Accepted": pill_cls = "accepted"
                elif status == "Needs changes": pill_cls = "needs"
                r0, r1, r2 = st.columns([6,2,2])
                with r0:
                    st.markdown(f"📄 {html.escape(fname)}  \n<span class='ann-small'>by {html.escape(uploader)} • {ts}</span><br><span class='status-pill {pill_cls}'>{html.escape(status)}</span>", unsafe_allow_html=True)
                with r1:
                    if role == "Tutor":
                        try:
                            idx = ["Pending", "Accepted", "Needs changes"].index(status)
                        except Exception:
                            idx = 0
                        new_status = st.selectbox("Status", ["Pending","Accepted","Needs changes"], index=idx, key=f"st_{fid}")
                        if st.button("Update", key=f"upd_{fid}"):
                            set_review_status_db(fid, new_status)
                            st.success("Status updated.")
                            st.rerun()
                with r2:
                    if role in ("CR", "Tutor"):
                        if st.button("Delete", key=f"del_file_{fid}"):
                            delete_note_record_and_file(fid)
                            st.success("File deleted.")
                            st.rerun()
    else:
        st.info("No folders available.")

# ---------------- CHATBOX ----------------
elif menu_choice == "💬 Chatbox":
    st.header("💬 Class Chatbox (group)")
    search = st.text_input("Search chat (username or text)", key="chat_search")
    msgs = fetch_chat_db(search=search)
    if not msgs:
        st.info("No messages yet.")
    else:
        # iterate top-level messages (oldest -> newest)
        for mid, usern, message, ts in msgs:
            # main message display
            if usern.lower() == username.lower():
                st.markdown(f"<div class='chat-right'><b>You</b> <div class='small-muted'>{ts}</div><div style='margin-top:6px'>{html.escape(message).replace('\\n','<br>')}</div></div>", unsafe_allow_html=True)
            else:
                st.markdown(f"<div class='chat-left'><b>{html.escape(usern)}</b> <div class='small-muted'>{ts}</div><div style='margin-top:6px'>{html.escape(message).replace('\\n','<br>')}</div></div>", unsafe_allow_html=True)

            # actions for the message
            action_cols = st.columns([1,1,6])
            with action_cols[0]:
                if st.button("Reply", key=f"reply_btn_{mid}"):
                    # prepare an empty input for this message id
                    st.session_state.reply_inputs[str(mid)] = ""
                    st.rerun()
            with action_cols[1]:
                if role in ("CR", "Tutor"):
                    if st.button("Delete", key=f"del_msg_{mid}"):
                        delete_chat_message_db(mid)
                        st.success("Message and its replies deleted.")
                        st.rerun()

            # show replies (one-level)
            replies = fetch_replies(mid)
            for rid, ruser, rmsg, rts in replies:
                if ruser.lower() == username.lower():
                    st.markdown(f"<div style='text-align:right;'><div class='reply-indent'><b>You</b> <div class='small-muted'>{rts}</div><div style='margin-top:6px'>{html.escape(rmsg).replace('\\n','<br>')}</div></div></div>", unsafe_allow_html=True)
                else:
                    st.markdown(f"<div class='reply-indent'><b>{html.escape(ruser)}</b> <div class='small-muted'>{rts}</div><div style='margin-top:6px'>{html.escape(rmsg).replace('\\n','<br>')}</div></div>", unsafe_allow_html=True)
                if role in ("CR", "Tutor"):
                    if st.button("Delete Reply", key=f"del_reply_{rid}"):
                        delete_chat_reply_db(rid)
                        st.success("Reply deleted.")
                        st.rerun()

            # reply input area (if user clicked reply for this message)
            key_str = str(mid)
            if key_str in st.session_state.reply_inputs:
                reply_val = st.text_input(f"Reply to {html.escape(usern)}", key=f"input_reply_{mid}", value=st.session_state.reply_inputs.get(key_str, ""))
                if st.button("Send Reply", key=f"send_reply_{mid}"):
                    if reply_val.strip():
                        add_chat_message_db(username, reply_val, parent_id=mid)
                        st.session_state.reply_inputs.pop(key_str, None)
                        st.success("Reply added.")
                        st.rerun()
                    else:
                        st.warning("Type a reply before sending.")
            st.markdown("---")

    # new message input
    new_msg = st.text_input("Type your message", key="chat_input")
    if st.button("Send"):
        if new_msg.strip():
            add_chat_message_db(username, new_msg, parent_id=None)
            # clear input if needed
            try:
                st.session_state.chat_input = ""
            except Exception:
                pass
            st.success("Message sent.")
            st.rerun()
        else:
            st.warning("Type a message first.")

    if role in ("CR", "Tutor"):
        if st.button("Clear Chat"):
            clear_chat_db()
            st.success("Chat cleared.")
            st.rerun()

# ---------------- Footer ----------------
st.markdown("""---  
<small>Made By Tarun | BSc AIML | Smart Class Companion © 2025</small>  
""", unsafe_allow_html=True)

