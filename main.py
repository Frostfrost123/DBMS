from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response, FileResponse, HTMLResponse # Added HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
import database as db
import shutil
import os
import uuid
import time
import urllib.parse
import zipfile 
import io 
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

db.init_db()

# Move uploads into the persistent data folder
UPLOAD_DIR = "data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/media", StaticFiles(directory=UPLOAD_DIR), name="media")
app.mount("/assets", StaticFiles(directory="assets"), name="assets")

def get_db():
    session = db.SessionLocal()
    try: yield session
    finally: session.close()

# --- SERVE THE FRONTEND TO THE BROWSER ---
@app.get("/")
async def serve_frontend():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

class LoginSchema(BaseModel):
    username: str
    password: str

class RenameSchema(BaseModel):
    new_name: str

class UniversalBulkDeleteSchema(BaseModel):
    file_ids: list[int] = []
    folder_ids: list[int] = []

class PasteSchema(BaseModel):
    file_ids: list[int]
    dest_event_id: int

class DownloadBatchSchema(BaseModel):
    file_ids: list[int] = []
    folder_ids: list[int] = []

class CalendarSchema(BaseModel):
    date_str: str
    description: str

@app.post("/login")
async def login(data: LoginSchema, session: Session = Depends(get_db)):
    user = session.query(db.User).filter(db.User.username == data.username).first()
    if not user or user.password != data.password:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"success": True, "role": user.role, "username": user.username}

@app.post("/register")
async def register(data: LoginSchema, session: Session = Depends(get_db)):
    existing_user = session.query(db.User).filter(db.User.username == data.username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Username already taken")
    
    new_user = db.User(username=data.username, password=data.password, role="student")
    session.add(new_user)
    session.commit()
    
    return {"success": True, "message": "Account created successfully"}

@app.post("/admin/upload")
async def upload(event_name: str = Form(...), files: list[UploadFile] = File(...), session: Session = Depends(get_db)):
    event = session.query(db.Event).filter(db.Event.name == event_name).first()
    if not event:
        event = db.Event(name=event_name)
        session.add(event); session.commit(); session.refresh(event)
    
    folder = os.path.join(UPLOAD_DIR, event_name)
    os.makedirs(folder, exist_ok=True)
    
    for file in files:
        unique_name = f"{int(time.time())}_{uuid.uuid4().hex[:6]}_{file.filename}"
        f_path = os.path.join(folder, unique_name)
        with open(f_path, "wb") as buf:
            shutil.copyfileobj(file.file, buf)
        
        new_file = db.File(filename=file.filename, file_path=f"{event_name}/{unique_name}", event_id=event.id)
        session.add(new_file)
    
    session.commit()
    return {"info": "Upload successful"}

@app.get("/search")
async def search(query: str = "", session: Session = Depends(get_db)):
    events = session.query(db.Event).filter(db.Event.name.contains(query)).all()
    return {"results": [{"id": e.id, "name": e.name} for e in events]}

@app.get("/suggested")
async def get_suggested(session: Session = Depends(get_db)):
    files = session.query(db.File).order_by(func.random()).limit(12).all()
    return [{"id": f.id, "filename": f.filename, "url": f"/media/{urllib.parse.quote(f.file_path, safe='/')}"} for f in files]

@app.get("/event/{event_id}/files")
async def get_files(event_id: int, session: Session = Depends(get_db)):
    files = session.query(db.File).filter(db.File.event_id == event_id).all()
    return [{"id": f.id, "filename": f.filename, "url": f"/media/{urllib.parse.quote(f.file_path, safe='/')}"} for f in files]

@app.put("/admin/event/{event_id}/rename")
async def rename_event(event_id: int, data: RenameSchema, session: Session = Depends(get_db)):
    event = session.query(db.Event).filter(db.Event.id == event_id).first()
    if not event: raise HTTPException(status_code=404, detail="Folder not found")
    
    old_folder = os.path.join(UPLOAD_DIR, event.name)
    new_folder = os.path.join(UPLOAD_DIR, data.new_name)
    if os.path.exists(old_folder) and not os.path.exists(new_folder):
        os.rename(old_folder, new_folder)
        for f in event.files:
            f.file_path = f.file_path.replace(f"{event.name}/", f"{data.new_name}/", 1)

    event.name = data.new_name
    session.commit()
    return {"success": True}

@app.put("/admin/file/{file_id}/rename")
async def rename_file(file_id: int, data: RenameSchema, session: Session = Depends(get_db)):
    file_record = session.query(db.File).filter(db.File.id == file_id).first()
    if not file_record: raise HTTPException(status_code=404, detail="File not found")
    file_record.filename = data.new_name
    session.commit()
    return {"success": True}

@app.post("/admin/files/paste")
async def paste_files(data: PasteSchema, session: Session = Depends(get_db)):
    dest_event = session.query(db.Event).filter(db.Event.id == data.dest_event_id).first()
    if not dest_event: raise HTTPException(status_code=404, detail="Destination folder not found")
    
    dest_folder = os.path.join(UPLOAD_DIR, dest_event.name)
    os.makedirs(dest_folder, exist_ok=True)
    
    files_to_copy = session.query(db.File).filter(db.File.id.in_(data.file_ids)).all()
    for f in files_to_copy:
        source_path = os.path.join(UPLOAD_DIR, f.file_path)
        if os.path.exists(source_path):
            unique_name = f"{int(time.time())}_{uuid.uuid4().hex[:6]}_{f.filename}"
            new_file_path_relative = f"{dest_event.name}/{unique_name}"
            new_full_path = os.path.join(UPLOAD_DIR, new_file_path_relative)
            
            shutil.copy2(source_path, new_full_path)
            new_file = db.File(filename=f.filename, file_path=new_file_path_relative, event_id=dest_event.id)
            session.add(new_file)
            
    session.commit()
    return {"success": True}

@app.post("/admin/bulk-delete")
async def bulk_delete_all(data: UniversalBulkDeleteSchema, session: Session = Depends(get_db)):
    if data.file_ids:
        files_to_delete = session.query(db.File).filter(db.File.id.in_(data.file_ids)).all()
        for f in files_to_delete:
            full_path = os.path.join(UPLOAD_DIR, f.file_path)
            if os.path.exists(full_path): os.remove(full_path)
            session.delete(f)
            
    if data.folder_ids:
        events_to_delete = session.query(db.Event).filter(db.Event.id.in_(data.folder_ids)).all()
        for event in events_to_delete:
            folder_path = os.path.join(UPLOAD_DIR, event.name)
            if os.path.exists(folder_path): shutil.rmtree(folder_path)
            session.delete(event)
            
    session.commit()
    return {"success": True}

@app.put("/admin/event/{event_id}/star")
async def toggle_star_event(event_id: int, session: Session = Depends(get_db)):
    event = session.query(db.Event).filter(db.Event.id == event_id).first()
    if not event: raise HTTPException(status_code=404, detail="Folder not found")
    event.is_starred = not event.is_starred
    session.commit()
    return {"success": True, "is_starred": event.is_starred}

@app.get("/starred")
async def get_starred(session: Session = Depends(get_db)):
    events = session.query(db.Event).filter(db.Event.is_starred == True).all()
    return [{"id": e.id, "name": e.name} for e in events]


@app.get("/calendar")
async def get_calendar(session: Session = Depends(get_db)):
    events = session.query(db.CalendarEvent).all()
    return {e.date_str: e.description for e in events}

@app.post("/admin/calendar")
async def save_calendar(data: CalendarSchema, session: Session = Depends(get_db)):
    ev = session.query(db.CalendarEvent).filter(db.CalendarEvent.date_str == data.date_str).first()
    if data.description.strip() == "":
        if ev: session.delete(ev)
    else:
        if ev:
            ev.description = data.description
        else:
            new_ev = db.CalendarEvent(date_str=data.date_str, description=data.description)
            session.add(new_ev)
    session.commit()
    return {"success": True}


@app.get("/download-single/{file_id}")
async def download_single(file_id: int, session: Session = Depends(get_db)):
    file_record = session.query(db.File).filter(db.File.id == file_id).first()
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")
    
    file_path = os.path.join(UPLOAD_DIR, file_record.file_path.replace("\\", "/"))
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File missing on server")
        
    return FileResponse(
        path=file_path, 
        filename=file_record.filename,
        media_type='application/octet-stream'
    )


def cleanup_temp_file(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass

@app.post("/download-batch")
async def download_batch(data: DownloadBatchSchema, background_tasks: BackgroundTasks, session: Session = Depends(get_db)):
    try:
        temp_zip_name = f"temp_export_{int(time.time())}_{uuid.uuid4().hex[:6]}.zip"
        temp_zip_path = os.path.join(UPLOAD_DIR, temp_zip_name)
        
        with zipfile.ZipFile(temp_zip_path, mode='w', compression=zipfile.ZIP_STORED) as zipf:
            added_any = False
            
            if data.file_ids:
                files = session.query(db.File).filter(db.File.id.in_(data.file_ids)).all()
                for f in files:
                    fpath = os.path.join(UPLOAD_DIR, f.file_path.replace("\\", "/"))
                    if os.path.exists(fpath):
                        zipf.write(fpath, arcname=f.filename)
                        added_any = True
                        
            if data.folder_ids:
                folders = session.query(db.Event).filter(db.Event.id.in_(data.folder_ids)).all()
                for folder in folders:
                    folder_path = os.path.join(UPLOAD_DIR, folder.name)
                    if os.path.exists(folder_path):
                        for root, _, filenames in os.walk(folder_path):
                            for filename in filenames:
                                file_path = os.path.join(root, filename)
                                arcname = os.path.join(folder.name, os.path.relpath(file_path, folder_path))
                                zipf.write(file_path, arcname=arcname)
                                added_any = True
            
            if not added_any:
                zipf.writestr("empty_download.txt", "No physical files were found on the server.")
                
        background_tasks.add_task(cleanup_temp_file, temp_zip_path)
        
        return FileResponse(
            path=temp_zip_path,
            filename=f"pup_cloud_export_{int(time.time())}.zip",
            media_type="application/zip"
        )
    except Exception as e:
        print(f"Error zipping files: {e}")
        raise HTTPException(status_code=500, detail="Failed to create ZIP file on the server.")
