import io
import re
import sqlite3
import uuid
import zipfile
from pathlib import Path

import imagehash
import numpy as np
import streamlit as st
from PIL import Image, ImageDraw, ImageFilter

APP_NAME = "Dreamy Image Asset Factory"
DB_FILE = "dreamy_image.db"
STORAGE_ROOT = Path("storage/packs")
STORAGE_ROOT.mkdir(parents=True, exist_ok=True)

BANNED_REPLACEMENTS = {
    "disney": "whimsical storybook fantasy",
    "hogwarts": "ancient magical academy",
    "lord of the rings": "epic mythic high fantasy",
    "marvel": "cinematic heroic fantasy",
    "star wars": "epic celestial science fantasy",
    "greg rutkowski": "ethereal digital fantasy painting with romantic atmospheric detail",
}

SIGNATURE_STYLE = (
    "extreme verticality, stacked landscapes, cascading tiers building from bottom to top, "
    "dual-source lighting with a high cosmic moon and starry twilight sky plus a low rising sun, "
    "dramatic golden sunbeams through glowing mist, dreamy ethereal atmosphere, fantasy surrealism, "
    "deep purples, twilight blues, magenta accents, glowing gold and emerald highlights, "
    "bioluminescent flora, hanging lanterns, ancient stone pathways, distant mythical structures, "
    "atmospheric perspective, chiaroscuro lighting, original fantasy landscape composition"
)

def db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_database():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS asset_packs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            raw_prompt TEXT NOT NULL,
            safe_prompt TEXT NOT NULL,
            price REAL NOT NULL DEFAULT 4.99,
            status TEXT NOT NULL DEFAULT 'Draft',
            master_path TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pack_components (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pack_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            component_tag TEXT NOT NULL,
            p_hash TEXT NOT NULL,
            storage_path TEXT NOT NULL,
            FOREIGN KEY(pack_id) REFERENCES asset_packs(id) ON DELETE CASCADE
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS customer_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pack_id INTEGER NOT NULL,
            access_code TEXT UNIQUE NOT NULL,
            payment_status TEXT NOT NULL DEFAULT 'Completed',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(pack_id) REFERENCES asset_packs(id)
        )
    """)
    conn.commit()
    conn.close()

def sanitize_prompt(user_text: str):
    cleaned = user_text.strip()
    replacements = []
    for banned, replacement in BANNED_REPLACEMENTS.items():
        if re.search(re.escape(banned), cleaned, flags=re.IGNORECASE):
            cleaned = re.sub(re.escape(banned), replacement, cleaned, flags=re.IGNORECASE)
            replacements.append(banned)
    return f"{cleaned}, {SIGNATURE_STYLE}", replacements

def prompt_seed(text: str) -> int:
    return abs(hash(text)) % (2**32)

def generate_local_preview(final_prompt: str, ratio: str) -> Image.Image:
    w, h = (576, 1024) if ratio == "9:16 Portrait" else (768, 768)
    rng = np.random.default_rng(prompt_seed(final_prompt))
    y = np.linspace(0, 1, h)[:, None]
    arr = np.zeros((h, w, 3), dtype=np.float32)
    arr[:, :, 0] = 35 + 110 * y
    arr[:, :, 1] = 15 + 45 * y
    arr[:, :, 2] = 85 + 120 * (1 - y)
    lower = np.clip((y - 0.58) / 0.42, 0, 1)
    arr[:, :, 0] += 95 * lower
    arr[:, :, 1] += 55 * lower
    arr += rng.normal(0, 5, (h, w, 1))
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    r = int(w * 0.11)
    mx, my = int(w * 0.72), int(h * 0.12)
    draw.ellipse((mx-r, my-r, mx+r, my+r), fill=(235, 220, 255, 210))
    for i, frac in enumerate((0.42, 0.58, 0.72, 0.84)):
        top = int(h * frac)
        inset = int(w * (0.08 + i * 0.03))
        draw.polygon([(inset, h), (inset, top+50), (w//2, top), (w-inset, top+80), (w-inset, h)], fill=(35, 22, 62, 235))
    draw.rounded_rectangle((int(w*.45), int(h*.34), int(w*.56), int(h*.87)), radius=max(4, w//80), fill=(135, 210, 245, 210))
    draw.line([(int(w*.12), int(h*.94)), (int(w*.45), int(h*.72)), (int(w*.57), int(h*.58))], fill=(255, 205, 95, 220), width=max(3, w//70))
    return img.filter(ImageFilter.GaussianBlur(radius=0.6))

def make_rgba(base, mask):
    rgba = base.convert("RGBA")
    rgba.putalpha(mask)
    return rgba

def near_duplicate(hash_value: str, threshold: int = 5):
    conn = db()
    rows = conn.execute("SELECT p_hash FROM pack_components").fetchall()
    conn.close()
    best = None
    for (existing,) in rows:
        try:
            distance = imagehash.hex_to_hash(hash_value) - imagehash.hex_to_hash(existing)
        except Exception:
            continue
        best = distance if best is None else min(best, distance)
        if distance <= threshold:
            return True, distance
    return False, best

def segment_layers(base):
    w, h = base.size
    fg_mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(fg_mask).polygon([(0, h), (0, int(h*.74)), (int(w*.35), int(h*.63)), (w, int(h*.80)), (w, h)], fill=255)
    fg_mask = fg_mask.filter(ImageFilter.GaussianBlur(radius=5))

    mid_mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mid_mask).rounded_rectangle((int(w*.26), int(h*.25), int(w*.78), int(h*.83)), radius=max(12, w//18), fill=255)
    mid_mask = mid_mask.filter(ImageFilter.GaussianBlur(radius=7))

    bg_mask = Image.new("L", (w, h), 255)
    candidates = {
        "Foreground_Path_Anchors.png": ("Foreground / Paths", make_rgba(base, fg_mask)),
        "Midground_Cascade_Structures.png": ("Midground / Waterfalls & Structures", make_rgba(base, mid_mask)),
        "Background_Sky_Atmosphere.png": ("Background / Sky & Atmosphere", make_rgba(base, bg_mask)),
    }

    out = {}
    for filename, (tag, img) in candidates.items():
        hsh = str(imagehash.phash(img.convert("RGB")))
        dup, distance = near_duplicate(hsh)
        out[filename] = {"tag": tag, "image": img, "hash": hsh, "near_duplicate": dup, "distance": distance}
    return out

def safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip())
    return value.strip("_") or "asset"

def save_pack_files(pack_id, master, assets):
    pack_dir = STORAGE_ROOT / f"pack_{pack_id}"
    assets_dir = pack_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    master_path = pack_dir / "master.png"
    master.save(master_path, "PNG")
    saved = []
    for filename, data in assets.items():
        clean = safe_filename(Path(filename).stem) + ".png"
        path = assets_dir / clean
        data["image"].save(path, "PNG")
        saved.append((clean, data["tag"], data["hash"], str(path)))
    return str(master_path), saved

def create_pack(title, raw_prompt, safe_prompt, price, master, assets):
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT INTO asset_packs (title, raw_prompt, safe_prompt, price, status) VALUES (?, ?, ?, ?, 'Draft')",
                (title, raw_prompt, safe_prompt, price))
    pack_id = cur.lastrowid
    master_path, saved_assets = save_pack_files(pack_id, master, assets)
    cur.execute("UPDATE asset_packs SET master_path=? WHERE id=?", (master_path, pack_id))
    for filename, tag, hsh, path in saved_assets:
        cur.execute("INSERT INTO pack_components (pack_id, filename, component_tag, p_hash, storage_path) VALUES (?, ?, ?, ?, ?)",
                    (pack_id, filename, tag, hsh, path))
    conn.commit()
    conn.close()
    return pack_id

def build_commercial_license(pack_id, pack_title, access_code):
    return f"""COMMERCIAL USE LICENSE AGREEMENT
================================
Product: {pack_title}
Asset Pack ID: {pack_id}
Order / License Reference: {access_code}

The purchaser receives a non-exclusive, non-transferable commercial-use license
for the digital assets contained in this package.

PERMITTED USES
- Personal and commercial composite artwork
- Modification, resizing, recoloring, cropping, and transformation
- Print, web, social, marketing, client work, packaging, and finished merchandise
- Sale of completed works where the licensed assets are part of a larger finished design

RESTRICTIONS
- Do not resell, redistribute, share, sublicense, or give away the original PNG files as standalone assets.
- Do not include the original source assets in another stock, clip-art, template, or asset-pack collection.
- Do not claim authorship of the underlying original source files.
- Do not make the original source files available for third-party download.
- This license is not transferable unless a separate license is issued.

GENERATIVE AI / ORIGINALITY NOTICE
Some or all visual materials may be created or processed using generative AI and
image-processing systems. Prompt filtering, segmentation, perceptual hashing,
and provenance records may reduce duplication and prohibited IP references, but
do not guarantee freedom from every possible third-party claim.

License Type: Commercial Use / Non-Exclusive / Non-Transferable
Generated automatically by {APP_NAME}.
""".strip()

def build_customer_zip(pack_id, pack_title, access_code):
    conn = db()
    rows = conn.execute("SELECT filename, component_tag, storage_path FROM pack_components WHERE pack_id=?", (pack_id,)).fetchall()
    conn.close()
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, _tag, storage_path in rows:
            p = Path(storage_path)
            if p.exists():
                zf.write(p, arcname=f"Assets/{filename}")
        zf.writestr("Commercial Use License Agreement.txt", build_commercial_license(pack_id, pack_title, access_code))
        zf.writestr("Pack Information.txt",
                    f"Pack: {pack_title}\nPack ID: {pack_id}\nOrder Reference: {access_code}\nTransparent Assets: {len(rows)}\n")
    return zbuf.getvalue()

def publish_pack(pack_id):
    conn = db()
    conn.execute("UPDATE asset_packs SET status='Active' WHERE id=?", (pack_id,))
    conn.commit()
    conn.close()

def simulate_purchase(pack_id):
    code = uuid.uuid4().hex[:20]
    conn = db()
    conn.execute("INSERT INTO customer_orders (pack_id, access_code, payment_status) VALUES (?, ?, 'Completed')", (pack_id, code))
    conn.commit()
    conn.close()
    return code

init_database()
st.set_page_config(page_title=APP_NAME, page_icon="🌌", layout="wide")
st.markdown("""
<style>
.block-container {max-width: 1200px; padding-top: 1rem;}
div[data-testid="stMetric"] {background: rgba(255,255,255,.04); padding: .7rem; border-radius: 12px;}
@media (max-width: 768px) { div[data-testid="stHorizontalBlock"] {flex-direction: column;} }
</style>
""", unsafe_allow_html=True)

params = st.query_params
mode = "📥 Customer Receipt" if "download_key" in params else st.sidebar.selectbox("Workspace", ["🏭 Creator Studio", "🛒 Public Storefront"])
st.title("🌌 Dreamy Image Asset Factory")
st.caption("Vertical fantasy composite generator, transparent-layer packer, provenance ledger, and storefront baseline.")

if mode == "🏭 Creator Studio":
    left, center, right = st.columns([1, 1.35, 1])
    with left:
        st.subheader("Build Controls")
        title = st.text_input("Pack title", "Ethereal Cascades Pack v1")
        price = st.number_input("Price ($)", min_value=0.99, value=4.99, step=0.50)
        prompt = st.text_area("Scene description", "A stone lookout tower beside cascading rivers and glowing trees", height=140)
        ratio = st.radio("Aspect ratio", ["9:16 Portrait", "1:1 Square"])
        generate = st.button("Generate Draft Pack", type="primary", use_container_width=True)

    if generate:
        safe_prompt, replacements = sanitize_prompt(prompt)
        master = generate_local_preview(safe_prompt, ratio)
        assets = segment_layers(master)
        pack_id = create_pack(title, prompt, safe_prompt, float(price), master, assets)
        st.session_state.update(latest_pack_id=pack_id, latest_master=master, latest_assets=assets, latest_replacements=replacements)

    with center:
        st.subheader("Canvas")
        if "latest_master" in st.session_state:
            tab1, tab2 = st.tabs(["Composite", "Exploded Layers"])
            with tab1:
                st.image(st.session_state["latest_master"], use_container_width=True)
            with tab2:
                for filename, data in st.session_state["latest_assets"].items():
                    st.markdown(f"**{data['tag']}**")
                    st.image(data["image"], use_container_width=True)
                    buf = io.BytesIO()
                    data["image"].save(buf, "PNG")
                    st.download_button("Download PNG", buf.getvalue(), file_name=filename, mime="image/png", key=f"dl_{filename}")
        else:
            st.info("Generate a draft pack to preview the composite and layers.")

    with right:
        st.subheader("Marketplace Packer")
        if "latest_pack_id" in st.session_state:
            pid = st.session_state["latest_pack_id"]
            st.metric("Draft Pack ID", pid)
            if st.session_state.get("latest_replacements"):
                st.warning("Sanitizer replaced: " + ", ".join(st.session_state["latest_replacements"]))
            for filename, data in st.session_state["latest_assets"].items():
                if data["near_duplicate"]:
                    st.warning(f"{filename}: near-duplicate warning (distance {data['distance']}).")
                else:
                    st.success(f"{filename}: no close local-ledger match.")
            if st.button("Approve & Publish", use_container_width=True):
                publish_pack(pid)
                st.success(f"Pack #{pid} is now active.")
        else:
            st.write("Nothing publishes until you approve it.")

elif mode == "🛒 Public Storefront":
    st.subheader("Public Storefront")
    conn = db()
    packs = conn.execute("SELECT id, title, raw_prompt, price, master_path FROM asset_packs WHERE status='Active' ORDER BY id DESC").fetchall()
    conn.close()
    if not packs:
        st.info("No published packs yet.")
    for pack_id, title, raw_prompt, price, master_path in packs:
        with st.container(border=True):
            c1, c2 = st.columns([1, 1.4])
            with c1:
                if master_path and Path(master_path).exists():
                    st.image(master_path, use_container_width=True)
            with c2:
                st.subheader(title)
                st.write(raw_prompt)
                st.metric("Commercial license", "$" + f"{price:.2f}")
                if st.button("Simulate Purchase", key=f"buy_{pack_id}", use_container_width=True):
                    code = simulate_purchase(pack_id)
                    st.success("Mock payment marked completed.")
                    st.code(f"?download_key={code}")

else:
    key = str(params.get("download_key", "")).strip()
    st.subheader("Secure Digital Fulfillment")
    conn = db()
    order = conn.execute("""
        SELECT o.access_code, p.id, p.title
        FROM customer_orders o
        JOIN asset_packs p ON p.id=o.pack_id
        WHERE o.access_code=? AND o.payment_status='Completed'
    """, (key,)).fetchone()
    conn.close()
    if not order:
        st.error("Invalid or unpaid download key.")
    else:
        code, pack_id, pack_title = order
        st.success(f"Access confirmed for {pack_title}.")
        bundle = build_customer_zip(pack_id, pack_title, code)
        st.download_button("Download Complete Commercial Pack (.ZIP)", bundle,
                           file_name=f"{safe_filename(pack_title)}_Commercial_Pack_{pack_id}.zip",
                           mime="application/zip", use_container_width=True)
        st.caption("ZIP includes the exact stored PNGs, pack information, and transaction-specific commercial license.")
