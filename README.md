# Dreamy Image Asset Factory

Phone-friendly Streamlit baseline for a vertical fantasy landscape asset generator and marketplace workflow.

## What works now
- Signature prompt engine with selected IP/style-term sanitization
- Offline runnable fantasy preview generator
- Foreground / midground / background transparent layer extraction baseline
- Exact asset storage by pack
- Perceptual hashing with near-duplicate distance checks
- SQLite pack, component, and order ledger
- Draft -> Approve -> Publish workflow
- Public storefront
- Mock purchase and secure download key
- Exact stored PNG fulfillment
- Commercial Use License Agreement automatically added to customer ZIP

## Run locally
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Open http://localhost:8501

## Important
The current image generation and segmentation are local runnable baselines. For production, replace those adapters with a licensed image-generation provider and a real segmentation stack such as SAM 2/rembg.

Generated content cannot be guaranteed legally risk-free or globally unique. The app uses filtering, provenance-style records, and similarity screening to reduce risk.
