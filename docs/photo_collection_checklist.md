# Manual Photo Collection Checklist: Indian Dark Store SKU Eval Set

This checklist provides the exact requirements for capturing/curating real consumer photos for **15–20 high-confusion Indian FMCG products**. 

---

## 1. Visual Characteristics Needed (Do's & Don'ts)

| Feature | DO Target (Real Consumer Capture) | DON'T Use (Marketing / Studio Renders) |
|---|---|---|
| **Lighting** | Natural indoor room lighting, fluorescent tubelight, slight packaging glare | Pure white seamless studio lighting with perfect zero-shadow illumination |
| **Angle** | Natural top-down, hand-held, slightly angled (~15°–45°), tilted | Perfect 90° orthographic front-facing render |
| **Surface** | Real kitchen table, wooden desk, kitchen counter, packer hand holding pack | 100% pure white (#FFFFFF) cut-out background |
| **Condition** | Normal store shelf handling: minor crinkles, packaging folds | Flawlessly inflated, CGI/rendered 3D pouch pack |
| **Angles per SKU**| **2 to 3 photos minimum:**<br>1. **Front view**<br>2. **Back view** (nutrition table & barcode)<br>3. **Angled / Tilted or In-hand** | Only 1 front studio shot (breaks when pack lands face-down) |

---

## 2. Priority High-Confusion SKU Master List

Organized into the 6 classic dark-store fulfillment mismatch pairs:

### Group 1: Lay's Color & Flavor Lookalikes (Identical 50g Weight)
*Risk: Swapped during peak-hour rush because packs weigh the exact same.*
* **`8901491101844`** — **Lay's India's Magic Masala** (Blue Pack, 50g)
  - Target Folder: `data/anchors/8901491101844/`
* **`8901491001168`** — **Lay's Classic Salted** (Yellow Pack, 50g)
  - Target Folder: `data/anchors/8901491001168/`
* **`8901491361026`** — **Lay's Spanish Tomato Tango** (Red Pack, 50g)
  - Target Folder: `data/anchors/8901491361026/`
* **`8906010500016`** — **Balaji Wafers Simply Salted** (Yellow Pack lookalike, 50g)
  - Target Folder: `data/anchors/8906010500016/`

### Group 2: Maggi Noodle Variants
*Risk: Rushed picker scans one packet multiple times and grabs mixed variants from adjacent bins.*
* **`8901063139329`** — **Maggi 2-Minute Masala Noodles** (Classic Yellow Pack, 70g)
  - Target Folder: `data/anchors/8901063139329/`
* **`8901063092853`** — **Maggi Special Masala Noodles / Veg Atta** (Yellow Pack lookalike, 70g)
  - Target Folder: `data/anchors/8901063092853/`
* **`89080153`** — **Maggi Masala-ae-Magic Seasoning** (Small Sachet, 6g)
  - Target Folder: `data/anchors/89080153/`

### Group 3: Parle-G Biscuit Lookalikes & Pack Sizes
*Risk: Multi-pack scanning shortcut or confusion between standard vs Gold.*
* **`8901719134845`** — **Parle-G Original Glucose Biscuits** (Standard Pack, 65g)
  - Target Folder: `data/anchors/8901719134845/`
* **`8901719135248`** — **Parle-G Gold Biscuits** (Gold/Yellow Pack, 100g)
  - Target Folder: `data/anchors/8901719135248/`
* **`8901719130014`** — **Parle Happy Happy Choco Chip** (Brown Pack, 60g)
  - Target Folder: `data/anchors/8901719130014/`
* **`8901719123870`** — **Parle-G Family Pack** (Larger format, 250g)
  - Target Folder: `data/anchors/8901719123870/`

### Group 4: Kurkure & Corn Puff Snacks
*Risk: Lookalike orange/green packaging with identical crinkle profile.*
* **`8901058000290`** — **Kurkure Masala Munch** (Orange Pack, 80g)
  - Target Folder: `data/anchors/8901058000290/`
* **`8901058005233`** — **Kurkure Green Chutney Style** (Green Pack, 75g)
  - Target Folder: `data/anchors/8901058005233/`
* **`8901058017687`** — **Kurkure Puffcorn Yummy Cheese** (Yellow-Orange Pack, 60g)
  - Target Folder: `data/anchors/8901058017687/`

### Group 5: Haldiram's Namkeen Lookalikes
*Risk: Red/yellow Indian namkeen pouches with same weight and nearly identical typography.*
* **`8901725118938`** — **Haldiram's Aloo Bhujia** (150g)
  - Target Folder: `data/anchors/8901725118938/`
* **`8901725016258`** — **Haldiram's Bhujia Sev** (150g)
  - Target Folder: `data/anchors/8901725016258/`

### Group 6: Dairy Pouches (Barcode Scanner Failure Prone)
*Risk: Thermal barcode labels peel off or smudge due to condensation/cold storage moisture.*
* **`8901262010016`** — **Amul Pasteurized Butter** (Carton/Foil Pack, 100g)
  - Target Folder: `data/anchors/8901262010016/`
* **`8901262200196`** — **Amul Fresh Malai Paneer** (Pouch, 200g)
  - Target Folder: `data/anchors/8901262200196/`
* **`8901262260121`** — **Amul Taaza Toned Milk Pouch** (Pouch, 500ml)
  - Target Folder: `data/anchors/8901262260121/`

### Group 7: Staples
* **`8904043901015`** — **Tata Salt Vacuum Evaporated** (1kg)
  - Target Folder: `data/anchors/8904043901015/`

---

## 3. How to Name and Save the Files

Inside each SKU folder in [`data/anchors/<barcode>/`](file:///d:/Downloads/projects/Instamart_QC/data/anchors), save the photos using simple descriptive names:

```text
data/anchors/8901491101844/
├── catalog_front.jpg        (keep existing front view)
├── user_back.jpg            (back of pack / nutrition table)
└── user_in_hand.jpg         (angled / hand-held view)
```

- Any file ending in `.jpg`, `.jpeg`, `.png`, or `.webp` is automatically recognized.
- Resolution: Anything from `400x400` up to full phone camera resolution (`3000x4000`) works fine.

---

## 4. Commands to Run Once Saved

When you're done downloading/saving the images, run these two commands in PowerShell:

```powershell
cd d:\Downloads\projects\Instamart_QC
.\.venv\Scripts\Activate.ps1

# 1. Rebuild the multi-view DINOv2 embedding bank
python scripts/build_embedding_bank.py --catalog data/catalog/skus.yaml --images data/anchors --output artifacts/sku_bank.npz

# 2. Run the retrieval test across all views
python scripts/demo_catalog_lookup.py
```

