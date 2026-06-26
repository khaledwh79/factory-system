from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import FileResponse
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime, timedelta
from passlib.context import CryptContext
from jose import JWTError, jwt
from pydantic import BaseModel
from typing import Optional, List
import uvicorn
import os

# ===================== إعداد قاعدة البيانات =====================
# استخدم DATABASE_URL من البيئة (Render PostgreSQL) أو SQLite للتطوير المحلي
SQLALCHEMY_DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./factory_system.db")
# Render يعطي postgres:// لكن SQLAlchemy يحتاج postgresql://
if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)
# SQLite يحتاج check_same_thread، PostgreSQL لا يحتاجه
_connect_args = {"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ===================== الإعدادات الأمنية =====================
SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-to-a-long-random-secret-key-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 8

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer()

# ===================== النماذج (Models) =====================
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    full_name = Column(String)
    password_hash = Column(String)
    role = Column(String, default="employee")
    created_at = Column(DateTime, default=datetime.utcnow)

class Material(Base):
    __tablename__ = "materials"
    id = Column(Integer, primary_key=True, index=True)
    name_ar = Column(String)
    name_en = Column(String)
    category = Column(String)
    quantity = Column(Float)
    min_quantity = Column(Float, default=0)
    unit = Column(String, default="وحدة")
    created_by = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String)
    full_name = Column(String)
    action = Column(String)
    details = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)

class WarehouseItem(Base):
    __tablename__ = "warehouse_items"
    id = Column(Integer, primary_key=True, index=True)
    warehouse_type = Column(String, index=True)
    name_ar = Column(String)
    name_en = Column(String, nullable=True)   # الاسم الإنجليزي
    unit = Column(String, default="كجم")
    min_quantity = Column(Float, default=0)
    created_by = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

class Batch(Base):
    __tablename__ = "batches"
    id = Column(Integer, primary_key=True, index=True)
    internal_batch_no = Column(String, unique=True, index=True)
    warehouse_type = Column(String, index=True)
    item_id = Column(Integer)
    item_name = Column(String)
    unit = Column(String)
    quantity = Column(Float)
    remaining_qty = Column(Float)
    supplier_name = Column(String, nullable=True)
    supplier_batch_no = Column(String, nullable=True)
    production_date = Column(DateTime, nullable=True)
    expiry_date = Column(DateTime, nullable=True)
    receiving_date = Column(DateTime, nullable=True)   # تاريخ الاستلام الفعلي
    received_by = Column(String)
    received_at = Column(DateTime, default=datetime.utcnow)
    notes = Column(Text, nullable=True)
    status = Column(String, default="active")

class IssuanceRecord(Base):
    __tablename__ = "issuance_records"
    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer)
    batch_no = Column(String)
    item_name = Column(String)
    warehouse_type = Column(String)
    quantity_issued = Column(Float)
    unit = Column(String, nullable=True)
    issued_by = Column(String)
    issued_at = Column(DateTime, default=datetime.utcnow)
    purpose = Column(String, nullable=True)
    notes = Column(Text, nullable=True)

class Branch(Base):
    __tablename__ = "branches"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    location = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class ProductionLink(Base):
    """ربط باتش المنتج النهائي بالخامات والتعبئة المستخدمة في إنتاجه"""
    __tablename__ = "production_links"
    id = Column(Integer, primary_key=True, index=True)
    finished_batch_id = Column(Integer)
    finished_batch_no = Column(String)
    finished_item_name = Column(String)
    raw_batch_id = Column(Integer)
    raw_batch_no = Column(String)
    raw_item_name = Column(String)
    warehouse_type = Column(String)       # raw / packaging
    quantity_used = Column(Float)
    unit = Column(String, nullable=True)
    linked_by = Column(String)
    linked_at = Column(DateTime, default=datetime.utcnow)
    notes = Column(Text, nullable=True)

class DistributionRecord(Base):
    """توزيع باتشات المنتج النهائي على الفروع"""
    __tablename__ = "distribution_records"
    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer)
    batch_no = Column(String)
    item_name = Column(String)
    unit = Column(String, nullable=True)
    branch_id = Column(Integer)
    branch_name = Column(String)
    quantity = Column(Float)
    distribution_date = Column(DateTime, default=datetime.utcnow)
    distributed_by = Column(String)
    notes = Column(Text, nullable=True)

Base.metadata.create_all(bind=engine)

# ===== ترقية قاعدة البيانات — إضافة الأعمدة الجديدة إن لم تكن موجودة =====
def migrate_db():
    # PostgreSQL يدعم IF NOT EXISTS في ALTER TABLE
    is_pg = not SQLALCHEMY_DATABASE_URL.startswith("sqlite")
    if is_pg:
        migrations = [
            "ALTER TABLE batches ADD COLUMN IF NOT EXISTS receiving_date TIMESTAMP",
            "ALTER TABLE warehouse_items ADD COLUMN IF NOT EXISTS name_en VARCHAR",
            "ALTER TABLE issuance_records ADD COLUMN IF NOT EXISTS unit VARCHAR",
        ]
    else:
        migrations = [
            "ALTER TABLE batches ADD COLUMN receiving_date DATETIME",
            "ALTER TABLE warehouse_items ADD COLUMN name_en VARCHAR",
            "ALTER TABLE issuance_records ADD COLUMN unit VARCHAR",
        ]
    with engine.connect() as conn:
        for stmt in migrations:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass  # العمود موجود بالفعل

migrate_db()

# ===================== بذر المواد الافتراضية =====================
def create_default_data():
    db = SessionLocal()
    try:
        if db.query(WarehouseItem).count() > 0:
            return  # البيانات موجودة مسبقاً

        raw_items = [
            # (name_ar, name_en, unit)
            ("دقيق", "Flour", "كيلو"),
            ("سمن", "Ghee / Clarified Butter", "كيلو"),
            ("زيت نباتي", "Vegetable Oil", "ليتر"),
            ("سكر", "Sugar", "كيلو"),
            ("عسل", "Honey", "كيلو"),
            ("فستق حلبي", "Pistachio", "كيلو"),
            ("كاجو", "Cashew", "كيلو"),
            ("لوز", "Almond", "كيلو"),
            ("جوز", "Walnut", "كيلو"),
            ("فول سوداني", "Peanut", "كيلو"),
            ("بندق", "Hazelnut", "كيلو"),
            ("عجوة", "Date Paste", "كيلو"),
            ("قرفة", "Cinnamon", "كيلو"),
            ("هيل", "Cardamom", "كيلو"),
            ("زعفران", "Saffron", "جرام"),
            ("ماء ورد", "Rose Water", "ليتر"),
            ("ماء زهر", "Orange Blossom Water", "ليتر"),
            ("قشطة", "Clotted Cream (Qishta)", "كيلو"),
            ("جبنة موتزاريلا", "Mozzarella Cheese", "كيلو"),
            ("جبنة عكاوي", "Akkawi Cheese", "كيلو"),
            ("نشا", "Starch / Cornstarch", "كيلو"),
            ("خميرة", "Yeast", "كيلو"),
            ("بيكنج بودر", "Baking Powder", "كيلو"),
            ("شراب سكر (قطر)", "Sugar Syrup", "ليتر"),
            ("بهارات مشكلة", "Mixed Spices", "كيلو"),
            ("جوز الهند", "Desiccated Coconut", "كيلو"),
        ]

        packaging_items = [
            # علب بلاستيك
            ("علبة بلاستيك 250 جرام", "Plastic Box 250g", "حبة"),
            ("علبة بلاستيك 500 جرام", "Plastic Box 500g", "حبة"),
            ("علبة بلاستيك 1 كيلو", "Plastic Box 1kg", "حبة"),
            ("علبة بلاستيك 2 كيلو", "Plastic Box 2kg", "حبة"),
            ("علبة بلاستيك مشكلة صغيرة", "Small Assorted Plastic Box", "حبة"),
            ("علبة بلاستيك مشكلة كبيرة", "Large Assorted Plastic Box", "حبة"),
            # علب معدن
            ("علبة معدن مستطيلة صغيرة", "Small Rectangular Metal Tin", "حبة"),
            ("علبة معدن مستطيلة كبيرة", "Large Rectangular Metal Tin", "حبة"),
            ("علبة معدن دائرية صغيرة", "Small Round Metal Tin", "حبة"),
            ("علبة معدن دائرية كبيرة", "Large Round Metal Tin", "حبة"),
            ("علبة معدن فاخرة مستطيلة", "Premium Rectangular Metal Tin", "حبة"),
            ("علبة معدن ذهبية", "Gold Metal Tin", "حبة"),
            # كراتين
            ("كرتون صغير", "Small Carton Box", "حبة"),
            ("كرتون وسط", "Medium Carton Box", "حبة"),
            ("كرتون كبير", "Large Carton Box", "حبة"),
            ("كرتون شحن", "Shipping Carton", "حبة"),
            # مواد التغليف
            ("شيكارة نايلون شفافة صغيرة", "Small Clear Nylon Bag", "شيكارة"),
            ("شيكارة نايلون شفافة وسط", "Medium Clear Nylon Bag", "شيكارة"),
            ("شيكارة نايلون شفافة كبيرة", "Large Clear Nylon Bag", "شيكارة"),
            ("شيكارة نايلون بيضاء صغيرة", "Small White Nylon Bag", "شيكارة"),
            ("شيكارة نايلون بيضاء كبيرة", "Large White Nylon Bag", "شيكارة"),
            ("شيكارة ورقية بنية صغيرة", "Small Brown Paper Bag", "شيكارة"),
            ("شيكارة ورقية بنية كبيرة", "Large Brown Paper Bag", "شيكارة"),
            # رقائق ومواد تبطين
            ("رقاقة ألومنيوم", "Aluminum Foil", "رول"),
            ("رقاقة نايلون لاصقة", "Cling Wrap", "رول"),
            ("ورق زبدة", "Parchment / Baking Paper", "رول"),
            ("فوم تبطين", "Foam Sheet Liner", "حبة"),
            ("ورق كريب أبيض", "White Crepe Paper", "رول"),
            ("ورق كريب ذهبي", "Gold Crepe Paper", "رول"),
            # أشرطة وأختام
            ("شريط لاصق شفاف", "Clear Adhesive Tape", "رول"),
            ("شريط لاصق بني", "Brown Adhesive Tape", "رول"),
            ("شريط تغليف مطبوع", "Printed Packaging Tape", "رول"),
            ("شريط ساتان أبيض", "White Satin Ribbon", "رول"),
            ("شريط ساتان ذهبي", "Gold Satin Ribbon", "رول"),
            ("شريط ساتان أحمر", "Red Satin Ribbon", "رول"),
            # ملصقات وطباعة
            ("ملصق طباعة صغير", "Small Printed Label Sticker", "رزمة"),
            ("ملصق طباعة كبير", "Large Printed Label Sticker", "رزمة"),
            ("ملصق باركود", "Barcode Label Sticker", "رزمة"),
            ("بطاقة هدية صغيرة", "Small Gift Tag", "رزمة"),
            ("بطاقة هدية كبيرة", "Large Gift Tag", "رزمة"),
            # صواني ومناسبات
            ("صينية كرتون مقوى دائرية", "Round Cardboard Tray", "حبة"),
            ("صينية كرتون مقوى مستطيلة", "Rectangular Cardboard Tray", "حبة"),
            ("طبق بلاستيك أبيض صغير", "Small White Plastic Plate", "حبة"),
            ("طبق بلاستيك أبيض كبير", "Large White Plastic Plate", "حبة"),
            ("صندوق هدايا فاخر أبيض", "Luxury White Gift Box", "حبة"),
            ("صندوق هدايا فاخر ذهبي", "Luxury Gold Gift Box", "حبة"),
            # مواد لزوجة / إغلاق
            ("سدادة حرارية للأغطية", "Heat Seal Lid", "حبة"),
            ("غطاء بلاستيك شفاف", "Clear Plastic Lid", "حبة"),
            ("شمع إغلاق", "Sealing Wax", "علبة"),
            ("مشبك بلاستيك إغلاق", "Plastic Closure Clip", "حبة"),
            # منتجات متنوعة
            ("ورق بوبير أبيض", "White Tissue Paper", "رزمة"),
            ("ورق بوبير ذهبي", "Gold Tissue Paper", "رزمة"),
            ("فيلم تغليف حراري", "Heat Shrink Wrap Film", "رول"),
            ("جيوب تغليف بلاستيك", "Plastic Wrap Pouches", "عبوة"),
            ("علب بلاستيك شفافة للعرض صغيرة", "Small Clear Display Plastic Boxes", "عبوة"),
            ("علب بلاستيك شفافة للعرض كبيرة", "Large Clear Display Plastic Boxes", "عبوة"),
            ("دبابيس تثبيت بلاستيك", "Plastic Fastening Pins", "عبوة"),
            ("قفازات بلاستيك للتعبئة", "Plastic Packaging Gloves", "عبوة"),
            ("صناديق هدايا ورقية متوسطة", "Medium Paper Gift Boxes", "حبة"),
            ("صناديق هدايا ورقية صغيرة", "Small Paper Gift Boxes", "حبة"),
            ("سلات هدايا بلاستيك صغيرة", "Small Plastic Gift Baskets", "حبة"),
            ("سلات هدايا بلاستيك كبيرة", "Large Plastic Gift Baskets", "حبة"),
            ("علب معدن مربعة صغيرة", "Small Square Metal Tins", "حبة"),
            ("علب معدن مربعة كبيرة", "Large Square Metal Tins", "حبة"),
            ("كبسولات ورقية للمعجنات صغيرة", "Small Paper Pastry Cups", "عبوة"),
            ("كبسولات ورقية للمعجنات كبيرة", "Large Paper Pastry Cups", "عبوة"),
            ("ورق شمع للفصل بين الطبقات", "Wax Paper for Layer Separation", "رزمة"),
            ("طبق فويل ألومنيوم صغير", "Small Aluminum Foil Tray", "حبة"),
            ("طبق فويل ألومنيوم كبير", "Large Aluminum Foil Tray", "حبة"),
            ("أكياس ورقية مزخرفة صغيرة", "Small Decorative Paper Bags", "حبة"),
            ("أكياس ورقية مزخرفة كبيرة", "Large Decorative Paper Bags", "حبة"),
            ("مطبوعات كتالوج المنتجات", "Product Catalog Prints", "رزمة"),
        ]

        # إضافة مواد خام
        for name_ar, name_en, unit in raw_items:
            db.add(WarehouseItem(
                warehouse_type="raw", name_ar=name_ar, name_en=name_en,
                unit=unit, min_quantity=0, created_by="system"
            ))

        # إضافة مواد تعبئة
        for name_ar, name_en, unit in packaging_items:
            db.add(WarehouseItem(
                warehouse_type="packaging", name_ar=name_ar, name_en=name_en,
                unit=unit, min_quantity=0, created_by="system"
            ))

        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[seed] خطأ في بذر البيانات: {e}")
    finally:
        db.close()

# ===================== Pydantic Schemas =====================
class LoginRequest(BaseModel):
    username: str
    password: str

class CreateUserRequest(BaseModel):
    username: str
    full_name: str
    password: str
    role: str

class UpdateRoleRequest(BaseModel):
    role: str

class UpdatePasswordRequest(BaseModel):
    new_password: str

class ChangeMyPasswordRequest(BaseModel):
    old_password: str
    new_password: str

class UpdateProfileRequest(BaseModel):
    full_name: str
    username: Optional[str] = None

class WarehouseItemCreate(BaseModel):
    warehouse_type: str
    name_ar: str
    name_en: Optional[str] = None
    unit: str = "كجم"
    min_quantity: float = 0

class ReceivingCreate(BaseModel):
    item_id: int
    warehouse_type: str
    quantity: float
    unit: Optional[str] = None             # وحدة الكمية (كيلو/جرام/لتر/عبوة/كرتون/...)
    supplier_name: Optional[str] = None
    supplier_batch_no: Optional[str] = None
    production_date: Optional[str] = None
    expiry_date: Optional[str] = None
    receiving_date: Optional[str] = None
    notes: Optional[str] = None

class IssuanceCreate(BaseModel):
    batch_id: int
    quantity_issued: float
    purpose: Optional[str] = None
    notes: Optional[str] = None

class BranchCreate(BaseModel):
    name: str
    location: Optional[str] = None

class ProductionLinkCreate(BaseModel):
    finished_batch_id: int
    raw_batch_id: int
    quantity_used: float
    notes: Optional[str] = None

class DistributionCreate(BaseModel):
    batch_id: int
    branch_id: int
    quantity: float
    notes: Optional[str] = None

# ===================== إعداد التطبيق =====================
app = FastAPI(title="نظام إدارة مصنع البقلاوة")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ===================== دوال المساعدة =====================
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db)
) -> User:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="توكن غير صالح")
    except JWTError:
        raise HTTPException(status_code=401, detail="توكن منتهي أو غير صالح")
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="المستخدم غير موجود")
    return user

def require_role(*roles):
    def checker(current_user: User = Depends(get_current_user)):
        if current_user.role not in roles:
            raise HTTPException(status_code=403, detail="ليس لديك صلاحية لهذا الإجراء")
        return current_user
    return checker

def log_action(db: Session, user: User, action: str, details: str):
    entry = AuditLog(username=user.username, full_name=user.full_name,
                     action=action, details=details)
    db.add(entry)
    db.commit()

def generate_batch_no(db: Session, warehouse_type: str) -> str:
    prefix = {"raw": "R", "packaging": "P", "finished": "F"}.get(warehouse_type, "X")
    count = db.query(Batch).filter(Batch.warehouse_type == warehouse_type).count()
    return f"{prefix}{str(count + 1).zfill(5)}"

def parse_date(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        return None

# ===================== تهيئة المدير الأول =====================
def create_default_admin():
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.username == "admin").first():
            admin = User(username="admin", full_name="المدير العام",
                         password_hash=hash_password("admin123"), role="admin")
            db.add(admin)
            db.commit()
    finally:
        db.close()

create_default_admin()
create_default_data()

# ===================== Endpoints: المصادقة =====================
@app.post("/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == req.username).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="اسم المستخدم أو كلمة المرور خاطئة")
    token = create_access_token({"sub": user.username, "role": user.role})
    log_action(db, user, "LOGIN", f"تسجيل دخول: {user.username}")
    return {"access_token": token, "token_type": "bearer",
            "user": {"id": user.id, "username": user.username,
                     "full_name": user.full_name, "role": user.role}}

@app.get("/auth/me")
def get_me(current_user: User = Depends(get_current_user)):
    return {"id": current_user.id, "username": current_user.username,
            "full_name": current_user.full_name, "role": current_user.role}

@app.put("/auth/change-password")
def change_my_password(req: ChangeMyPasswordRequest,
                       current_user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    if not verify_password(req.old_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="كلمة المرور الحالية غير صحيحة")
    current_user.password_hash = hash_password(req.new_password)
    db.commit()
    log_action(db, current_user, "CHANGE_PASSWORD", "غيّر كلمة مروره")
    return {"status": "updated"}

@app.put("/auth/update-profile")
def update_my_profile(req: UpdateProfileRequest,
                      current_user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    if not req.full_name.strip():
        raise HTTPException(status_code=400, detail="الاسم لا يمكن أن يكون فارغاً")
    old_name = current_user.full_name
    current_user.full_name = req.full_name.strip()
    new_username = current_user.username
    if req.username and req.username.strip():
        new_username = req.username.strip()
        if new_username != current_user.username:
            existing = db.query(User).filter(User.username == new_username).first()
            if existing:
                raise HTTPException(status_code=400, detail="اسم المستخدم مستخدم بالفعل")
            old_username = current_user.username
            current_user.username = new_username
            log_action(db, current_user, "CHANGE_USERNAME", f"غيّر اسم المستخدم من '{old_username}' إلى '{new_username}'")
    if current_user.full_name != old_name:
        log_action(db, current_user, "CHANGE_PROFILE", f"غيّر اسمه من '{old_name}' إلى '{req.full_name}'")
    db.commit()
    return {"status": "updated", "full_name": current_user.full_name, "username": current_user.username}

# ===================== Endpoints: المستخدمون =====================
@app.get("/users/")
def list_users(current_user: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    users = db.query(User).all()
    return [{"id": u.id, "username": u.username, "full_name": u.full_name,
             "role": u.role, "created_at": u.created_at} for u in users]

@app.post("/users/")
def create_user(req: CreateUserRequest, current_user: User = Depends(require_role("admin")),
                db: Session = Depends(get_db)):
    if req.role not in ["admin", "supervisor", "employee"]:
        raise HTTPException(status_code=400, detail="الدور غير صالح")
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(status_code=400, detail="اسم المستخدم موجود مسبقاً")
    user = User(username=req.username, full_name=req.full_name,
                password_hash=hash_password(req.password), role=req.role)
    db.add(user)
    db.commit()
    log_action(db, current_user, "ADD_USER", f"أضاف مستخدم: {req.username} (دور: {req.role})")
    return {"status": "created", "id": user.id}

@app.put("/users/{user_id}/role")
def update_user_role(user_id: int, req: UpdateRoleRequest,
                     current_user: User = Depends(require_role("admin")),
                     db: Session = Depends(get_db)):
    if req.role not in ["admin", "supervisor", "employee"]:
        raise HTTPException(status_code=400, detail="الدور غير صالح")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="المستخدم غير موجود")
    old = user.role
    user.role = req.role
    db.commit()
    log_action(db, current_user, "CHANGE_ROLE", f"غيّر دور {user.username} من {old} إلى {req.role}")
    return {"status": "updated"}

@app.put("/users/{user_id}/password")
def update_user_password(user_id: int, req: UpdatePasswordRequest,
                         current_user: User = Depends(require_role("admin")),
                         db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="المستخدم غير موجود")
    user.password_hash = hash_password(req.new_password)
    db.commit()
    log_action(db, current_user, "CHANGE_PASSWORD", f"غيّر كلمة مرور: {user.username}")
    return {"status": "updated"}

@app.delete("/users/{user_id}")
def delete_user(user_id: int, current_user: User = Depends(require_role("admin")),
                db: Session = Depends(get_db)):
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="لا يمكنك حذف حسابك الخاص")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="المستخدم غير موجود")
    uname = user.username
    db.delete(user)
    db.commit()
    log_action(db, current_user, "DELETE_USER", f"حذف المستخدم: {uname}")
    return {"status": "deleted"}

# ===================== Endpoints: المواد (النظام القديم) =====================
@app.get("/materials/")
def get_all_materials(search: Optional[str] = Query(None), category: Optional[str] = Query(None),
                      low_stock: Optional[bool] = Query(None),
                      current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Material)
    if search:
        q = q.filter(Material.name_ar.contains(search) | Material.name_en.contains(search) |
                     Material.category.contains(search))
    if category:
        q = q.filter(Material.category == category)
    if low_stock:
        q = q.filter(Material.quantity <= Material.min_quantity)
    return q.all()

@app.get("/materials/categories/")
def get_categories(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Material.category).distinct().all()
    return [r[0] for r in rows if r[0]]

@app.get("/materials/stats/")
def get_stats(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    materials = db.query(Material).all()
    users_count = db.query(User).count()
    low_stock = [m for m in materials if m.quantity <= m.min_quantity and m.min_quantity > 0]
    by_category = {}
    for m in materials:
        cat = m.category or "غير محدد"
        if cat not in by_category:
            by_category[cat] = {"count": 0, "total_qty": 0}
        by_category[cat]["count"] += 1
        by_category[cat]["total_qty"] += m.quantity
    now = datetime.utcnow()
    expiring_soon = db.query(Batch).filter(
        Batch.status == "active", Batch.expiry_date != None,
        Batch.expiry_date <= now + timedelta(days=30)).count()
    return {"total_materials": len(materials), "total_quantity": sum(m.quantity for m in materials),
            "low_stock_count": len(low_stock), "users_count": users_count,
            "by_category": by_category, "expiring_soon": expiring_soon}

@app.post("/materials/")
def add_material(name_ar: str, name_en: str, category: str, quantity: float,
                 min_quantity: float = 0, unit: str = "وحدة",
                 current_user: User = Depends(require_role("admin", "supervisor")),
                 db: Session = Depends(get_db)):
    mat = Material(name_ar=name_ar, name_en=name_en, category=category,
                   quantity=quantity, min_quantity=min_quantity, unit=unit,
                   created_by=current_user.username)
    db.add(mat)
    db.commit()
    log_action(db, current_user, "ADD_MATERIAL",
               f"أضاف مادة: {name_ar} | الكمية: {quantity} {unit}")
    return {"status": "saved"}

@app.put("/materials/{m_id}")
def update_material(m_id: int, quantity: float,
                    current_user: User = Depends(require_role("admin", "supervisor")),
                    db: Session = Depends(get_db)):
    mat = db.query(Material).filter(Material.id == m_id).first()
    if not mat:
        raise HTTPException(status_code=404, detail="المادة غير موجودة")
    old = mat.quantity
    mat.quantity = quantity
    db.commit()
    log_action(db, current_user, "EDIT_MATERIAL", f"عدّل كمية: {mat.name_ar} من {old} إلى {quantity}")
    return {"status": "updated"}

@app.delete("/materials/{m_id}")
def delete_material(m_id: int, current_user: User = Depends(require_role("admin")),
                    db: Session = Depends(get_db)):
    mat = db.query(Material).filter(Material.id == m_id).first()
    if not mat:
        raise HTTPException(status_code=404, detail="المادة غير موجودة")
    name = mat.name_ar
    db.delete(mat)
    db.commit()
    log_action(db, current_user, "DELETE_MATERIAL", f"حذف المادة: {name}")
    return {"status": "deleted"}

# ===================== Endpoints: سجل العمليات =====================
@app.get("/audit/")
def get_audit_log(limit: int = Query(200, le=500),
                  current_user: User = Depends(require_role("admin")),
                  db: Session = Depends(get_db)):
    logs = db.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit).all()
    return [{"id": l.id, "username": l.username, "full_name": l.full_name,
             "action": l.action, "details": l.details, "timestamp": l.timestamp} for l in logs]

# ===================== Endpoints: ERP - تعريف المواد =====================
@app.get("/warehouse/items/")
def get_warehouse_items(warehouse_type: Optional[str] = Query(None),
                        current_user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    q = db.query(WarehouseItem)
    if warehouse_type:
        q = q.filter(WarehouseItem.warehouse_type == warehouse_type)
    return [{"id": i.id, "warehouse_type": i.warehouse_type,
             "name_ar": i.name_ar, "name_en": i.name_en,
             "unit": i.unit, "min_quantity": i.min_quantity} for i in q.all()]

@app.post("/warehouse/items/")
def add_warehouse_item(req: WarehouseItemCreate,
                       current_user: User = Depends(require_role("admin", "supervisor")),
                       db: Session = Depends(get_db)):
    item = WarehouseItem(warehouse_type=req.warehouse_type, name_ar=req.name_ar,
                         name_en=req.name_en, unit=req.unit,
                         min_quantity=req.min_quantity, created_by=current_user.username)
    db.add(item)
    db.commit()
    log_action(db, current_user, "ADD_WAREHOUSE_ITEM",
               f"أضاف مادة [{req.warehouse_type}]: {req.name_ar}" + (f" / {req.name_en}" if req.name_en else ""))
    return {"status": "created", "id": item.id}

@app.delete("/warehouse/items/{item_id}")
def delete_warehouse_item(item_id: int,
                          current_user: User = Depends(require_role("admin")),
                          db: Session = Depends(get_db)):
    item = db.query(WarehouseItem).filter(WarehouseItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="المادة غير موجودة")
    db.delete(item)
    db.commit()
    return {"status": "deleted"}

# ===================== Endpoints: ERP - الاستلام =====================
@app.post("/receiving/")
def receive_batch(req: ReceivingCreate,
                  current_user: User = Depends(require_role("admin", "supervisor")),
                  db: Session = Depends(get_db)):
    item = db.query(WarehouseItem).filter(WarehouseItem.id == req.item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="المادة غير معرّفة في المخزن")
    batch_no = generate_batch_no(db, req.warehouse_type)
    batch = Batch(
        internal_batch_no=batch_no,
        warehouse_type=req.warehouse_type,
        item_id=req.item_id,
        item_name=item.name_ar,
        unit=req.unit if req.unit and req.unit.strip() else item.unit,
        quantity=req.quantity,
        remaining_qty=req.quantity,
        supplier_name=req.supplier_name,
        supplier_batch_no=req.supplier_batch_no,
        production_date=parse_date(req.production_date),
        expiry_date=parse_date(req.expiry_date),
        receiving_date=parse_date(req.receiving_date),
        received_by=current_user.username,
        notes=req.notes
    )
    db.add(batch)
    db.commit()
    log_action(db, current_user, "RECEIVE_BATCH",
               f"استلم باتش {batch_no} | {item.name_ar} | الكمية: {req.quantity} {req.unit or item.unit} | المورد: {req.supplier_name or '-'}")
    return {"status": "received", "batch_no": batch_no, "batch_id": batch.id}

@app.get("/receiving/")
def get_batches(warehouse_type: Optional[str] = Query(None),
                status: Optional[str] = Query(None),
                search: Optional[str] = Query(None),
                expiring_days: Optional[int] = Query(None),
                limit: int = Query(200, le=500),
                current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    q = db.query(Batch)
    if warehouse_type:
        q = q.filter(Batch.warehouse_type == warehouse_type)
    if status:
        q = q.filter(Batch.status == status)
    if search:
        q = q.filter(Batch.item_name.contains(search) | Batch.internal_batch_no.contains(search))
    if expiring_days is not None:
        deadline = datetime.utcnow() + timedelta(days=expiring_days)
        q = q.filter(Batch.expiry_date != None, Batch.expiry_date <= deadline, Batch.status == "active")
    batches = q.order_by(Batch.received_at.desc()).limit(limit).all()
    result = []
    for b in batches:
        days_left = None
        if b.expiry_date:
            days_left = (b.expiry_date - datetime.utcnow()).days
        result.append({
            "id": b.id,
            "internal_batch_no": b.internal_batch_no,
            "warehouse_type": b.warehouse_type,
            "item_id": b.item_id,
            "item_name": b.item_name,
            "unit": b.unit,
            "quantity": b.quantity,
            "remaining_qty": b.remaining_qty,
            "supplier_name": b.supplier_name,
            "supplier_batch_no": b.supplier_batch_no,
            "production_date": b.production_date,
            "expiry_date": b.expiry_date,
            "receiving_date": b.receiving_date,
            "days_to_expiry": days_left,
            "received_by": b.received_by,
            "received_at": b.received_at,
            "status": b.status,
            "notes": b.notes,
        })
    return result

# ===================== Endpoints: ERP - الصرف =====================
@app.post("/issuance/")
def issue_from_batch(req: IssuanceCreate,
                     current_user: User = Depends(require_role("admin", "supervisor")),
                     db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.id == req.batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="الباتش غير موجود")
    if batch.status != "active":
        raise HTTPException(status_code=400, detail="هذا الباتش غير نشط")
    if req.quantity_issued > batch.remaining_qty:
        raise HTTPException(status_code=400,
            detail=f"الكمية المطلوبة ({req.quantity_issued}) أكبر من المتاح ({batch.remaining_qty})")
    batch.remaining_qty -= req.quantity_issued
    if batch.remaining_qty <= 0:
        batch.status = "depleted"
        batch.remaining_qty = 0
    record = IssuanceRecord(batch_id=batch.id, batch_no=batch.internal_batch_no,
                            item_name=batch.item_name, warehouse_type=batch.warehouse_type,
                            quantity_issued=req.quantity_issued, unit=batch.unit,
                            issued_by=current_user.username,
                            purpose=req.purpose, notes=req.notes)
    db.add(record)
    db.commit()
    log_action(db, current_user, "ISSUE_BATCH",
               f"صرف {req.quantity_issued} {batch.unit} من باتش {batch.internal_batch_no} | الغرض: {req.purpose or '-'}")
    return {"status": "issued", "batch_no": batch.internal_batch_no, "remaining": batch.remaining_qty}

@app.get("/issuance/")
def get_issuance_records(warehouse_type: Optional[str] = Query(None),
                         limit: int = Query(200, le=500),
                         current_user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    q = db.query(IssuanceRecord)
    if warehouse_type:
        q = q.filter(IssuanceRecord.warehouse_type == warehouse_type)
    records = q.order_by(IssuanceRecord.issued_at.desc()).limit(limit).all()
    return [{"id": r.id, "batch_no": r.batch_no, "item_name": r.item_name,
             "warehouse_type": r.warehouse_type, "quantity_issued": r.quantity_issued,
             "unit": r.unit, "issued_by": r.issued_by, "issued_at": r.issued_at,
             "purpose": r.purpose, "notes": r.notes} for r in records]

# ===================== Endpoints: ERP - الفروع =====================
@app.get("/branches/")
def get_branches(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [{"id": b.id, "name": b.name, "location": b.location, "created_at": b.created_at}
            for b in db.query(Branch).all()]

@app.post("/branches/")
def add_branch(req: BranchCreate, current_user: User = Depends(require_role("admin")),
               db: Session = Depends(get_db)):
    branch = Branch(name=req.name, location=req.location)
    db.add(branch)
    db.commit()
    log_action(db, current_user, "ADD_BRANCH", f"أضاف فرع: {req.name}")
    return {"status": "created", "id": branch.id}

@app.delete("/branches/{branch_id}")
def delete_branch(branch_id: int, current_user: User = Depends(require_role("admin")),
                  db: Session = Depends(get_db)):
    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="الفرع غير موجود")
    db.delete(branch)
    db.commit()
    log_action(db, current_user, "DELETE_BRANCH", f"حذف فرع: {branch.name}")
    return {"status": "deleted"}

# ===================== Endpoints: ERP - ربط الإنتاج (BOM) =====================
@app.post("/production-links/")
def add_production_link(req: ProductionLinkCreate,
                        current_user: User = Depends(require_role("admin", "supervisor")),
                        db: Session = Depends(get_db)):
    finished = db.query(Batch).filter(Batch.id == req.finished_batch_id,
                                       Batch.warehouse_type == "finished").first()
    if not finished:
        raise HTTPException(status_code=404, detail="باتش المنتج النهائي غير موجود")
    raw = db.query(Batch).filter(Batch.id == req.raw_batch_id).first()
    if not raw:
        raise HTTPException(status_code=404, detail="باتش المادة الخام غير موجود")
    link = ProductionLink(
        finished_batch_id=finished.id,
        finished_batch_no=finished.internal_batch_no,
        finished_item_name=finished.item_name,
        raw_batch_id=raw.id,
        raw_batch_no=raw.internal_batch_no,
        raw_item_name=raw.item_name,
        warehouse_type=raw.warehouse_type,
        quantity_used=req.quantity_used,
        unit=raw.unit,
        linked_by=current_user.username,
        notes=req.notes
    )
    db.add(link)
    db.commit()
    log_action(db, current_user, "PRODUCTION_LINK",
               f"ربط {finished.internal_batch_no} ← {raw.internal_batch_no} ({req.quantity_used} {raw.unit})")
    return {"status": "linked", "id": link.id}

@app.get("/production-links/")
def get_production_links(finished_batch_id: Optional[int] = Query(None),
                          current_user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    q = db.query(ProductionLink)
    if finished_batch_id:
        q = q.filter(ProductionLink.finished_batch_id == finished_batch_id)
    links = q.order_by(ProductionLink.linked_at.desc()).all()
    return [{"id": l.id, "finished_batch_id": l.finished_batch_id,
             "finished_batch_no": l.finished_batch_no, "finished_item_name": l.finished_item_name,
             "raw_batch_id": l.raw_batch_id, "raw_batch_no": l.raw_batch_no,
             "raw_item_name": l.raw_item_name, "warehouse_type": l.warehouse_type,
             "quantity_used": l.quantity_used, "unit": l.unit,
             "linked_by": l.linked_by, "linked_at": l.linked_at, "notes": l.notes} for l in links]

@app.delete("/production-links/{link_id}")
def delete_production_link(link_id: int,
                            current_user: User = Depends(require_role("admin", "supervisor")),
                            db: Session = Depends(get_db)):
    link = db.query(ProductionLink).filter(ProductionLink.id == link_id).first()
    if not link:
        raise HTTPException(status_code=404, detail="الرابط غير موجود")
    db.delete(link)
    db.commit()
    return {"status": "deleted"}

# ===================== Endpoints: ERP - التوزيع =====================
@app.post("/distribution/")
def distribute_batch(req: DistributionCreate,
                     current_user: User = Depends(require_role("admin", "supervisor")),
                     db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.id == req.batch_id,
                                    Batch.warehouse_type == "finished").first()
    if not batch:
        raise HTTPException(status_code=404, detail="الباتش غير موجود أو ليس منتجاً نهائياً")
    if batch.status != "active":
        raise HTTPException(status_code=400, detail="هذا الباتش غير نشط")
    if req.quantity > batch.remaining_qty:
        raise HTTPException(status_code=400,
            detail=f"الكمية ({req.quantity}) أكبر من المتاح ({batch.remaining_qty})")
    branch = db.query(Branch).filter(Branch.id == req.branch_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="الفرع غير موجود")
    batch.remaining_qty -= req.quantity
    if batch.remaining_qty <= 0:
        batch.status = "depleted"
        batch.remaining_qty = 0
    rec = DistributionRecord(batch_id=batch.id, batch_no=batch.internal_batch_no,
                              item_name=batch.item_name, unit=batch.unit,
                              branch_id=branch.id, branch_name=branch.name,
                              quantity=req.quantity, distributed_by=current_user.username,
                              notes=req.notes)
    db.add(rec)
    db.commit()
    log_action(db, current_user, "DISTRIBUTE",
               f"وزّع {req.quantity} {batch.unit} من باتش {batch.internal_batch_no} للفرع {branch.name}")
    return {"status": "distributed", "batch_no": batch.internal_batch_no, "remaining": batch.remaining_qty}

@app.get("/distribution/")
def get_distributions(batch_id: Optional[int] = Query(None),
                      branch_id: Optional[int] = Query(None),
                      current_user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    q = db.query(DistributionRecord)
    if batch_id:
        q = q.filter(DistributionRecord.batch_id == batch_id)
    if branch_id:
        q = q.filter(DistributionRecord.branch_id == branch_id)
    recs = q.order_by(DistributionRecord.distribution_date.desc()).all()
    return [{"id": r.id, "batch_id": r.batch_id, "batch_no": r.batch_no,
             "item_name": r.item_name, "unit": r.unit, "branch_id": r.branch_id,
             "branch_name": r.branch_name, "quantity": r.quantity,
             "distribution_date": r.distribution_date, "distributed_by": r.distributed_by,
             "notes": r.notes} for r in recs]

# ===================== Endpoints: ERP - التتبع =====================
@app.get("/traceability/{batch_no}")
def trace_batch(batch_no: str,
                current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.internal_batch_no == batch_no).first()
    if not batch:
        raise HTTPException(status_code=404, detail=f"الباتش {batch_no} غير موجود")
    result = {
        "batch_no": batch.internal_batch_no,
        "item_name": batch.item_name,
        "warehouse_type": batch.warehouse_type,
        "unit": batch.unit,
        "quantity": batch.quantity,
        "remaining_qty": batch.remaining_qty,
        "status": batch.status,
        "expiry_date": batch.expiry_date,
        "receiving_date": batch.receiving_date,
        "supplier_name": batch.supplier_name,
        "supplier_batch_no": batch.supplier_batch_no,
        "used_in_finished": [],
        "made_from": [],
        "distributed_to": [],
    }
    # تتبع للأمام: مواد خام/تعبئة → منتجات نهائية
    if batch.warehouse_type in ("raw", "packaging"):
        links = db.query(ProductionLink).filter(
            ProductionLink.raw_batch_id == batch.id).all()
        result["used_in_finished"] = [
            {"finished_batch_no": l.finished_batch_no,
             "finished_item_name": l.finished_item_name,
             "quantity_used": l.quantity_used, "unit": l.unit} for l in links]
    # تتبع للخلف: منتج نهائي → المواد الخام المستخدمة
    if batch.warehouse_type == "finished":
        links = db.query(ProductionLink).filter(
            ProductionLink.finished_batch_id == batch.id).all()
        result["made_from"] = [
            {"raw_batch_no": l.raw_batch_no, "raw_item_name": l.raw_item_name,
             "warehouse_type": l.warehouse_type,
             "quantity_used": l.quantity_used, "unit": l.unit} for l in links]
        dists = db.query(DistributionRecord).filter(
            DistributionRecord.batch_id == batch.id).all()
        result["distributed_to"] = [
            {"branch_name": d.branch_name, "quantity": d.quantity,
             "unit": d.unit, "distribution_date": d.distribution_date,
             "distributed_by": d.distributed_by} for d in dists]
    return result

# ===================== Endpoints: ERP - التقارير =====================
@app.get("/reports/inventory/{warehouse_type}")
def report_inventory(warehouse_type: str,
                     current_user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    batches = db.query(Batch).filter(
        Batch.warehouse_type == warehouse_type,
        Batch.status == "active"
    ).order_by(Batch.expiry_date).all()
    items = db.query(WarehouseItem).filter(
        WarehouseItem.warehouse_type == warehouse_type).all()
    item_totals = {}
    for b in batches:
        if b.item_name not in item_totals:
            item_totals[b.item_name] = {"total": 0, "unit": b.unit, "batches": 0}
        item_totals[b.item_name]["total"] += b.remaining_qty
        item_totals[b.item_name]["batches"] += 1
    alerts = []
    for item in items:
        total = item_totals.get(item.name_ar, {}).get("total", 0)
        if item.min_quantity > 0 and total < item.min_quantity:
            alerts.append({"item": item.name_ar, "current": total,
                           "minimum": item.min_quantity, "unit": item.unit})
    return {
        "warehouse_type": warehouse_type,
        "active_batches": len(batches),
        "item_totals": [{"item_name": k, **v} for k, v in item_totals.items()],
        "low_stock_alerts": alerts,
        "batches": [{"batch_no": b.internal_batch_no, "item_name": b.item_name,
                     "remaining_qty": b.remaining_qty, "unit": b.unit,
                     "expiry_date": b.expiry_date, "supplier_name": b.supplier_name,
                     "supplier_batch_no": b.supplier_batch_no} for b in batches]
    }

@app.get("/reports/overview")
def report_overview(current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    total_raw = db.query(Batch).filter(Batch.warehouse_type=="raw", Batch.status=="active").count()
    total_pkg = db.query(Batch).filter(Batch.warehouse_type=="packaging", Batch.status=="active").count()
    total_fin = db.query(Batch).filter(Batch.warehouse_type=="finished", Batch.status=="active").count()
    total_dist = db.query(DistributionRecord).count()
    total_links = db.query(ProductionLink).c