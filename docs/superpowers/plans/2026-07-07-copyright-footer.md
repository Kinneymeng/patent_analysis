# Copyright Footer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an always-visible, unobtrusive copyright line centered at the bottom of the EXE main window.

**Architecture:** Define the approved copyright text once in the GUI module, then add a non-interactive `CTkLabel` as a third top-level grid row beneath the existing main panel. Keep the existing window dimensions and let the weighted main-panel row absorb the small footer height.

**Tech Stack:** Python 3.10+, CustomTkinter, unittest

---

### Task 1: Add and verify the copyright footer

**Files:**
- Modify: `tests/test_gui.py`
- Modify: `patent_summarizer/gui.py`

- [ ] **Step 1: Write the failing copyright text test**

Add `COPYRIGHT_TEXT` to the imports from `patent_summarizer.gui`, then add this test to `GuiCommandTests`:

```python
def test_copyright_text_uses_approved_owner_and_year(self):
    self.assertEqual(
        COPYRIGHT_TEXT,
        "© 2026 平台及模组开发部 · 线控底盘所 版权所有",
    )
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
python -m unittest tests.test_gui.GuiCommandTests.test_copyright_text_uses_approved_owner_and_year
```

Expected: `ImportError` because `COPYRIGHT_TEXT` does not exist yet.

- [ ] **Step 3: Define the text and add the footer label**

In `patent_summarizer/gui.py`, define the module constant with the other GUI constants:

```python
COPYRIGHT_TEXT = "© 2026 平台及模组开发部 · 线控底盘所 版权所有"
```

In `PatentSummarizerApp._build_layout`, reduce the main panel's bottom gap and add the label after the panel is placed:

```python
panel.grid(row=1, column=0, sticky="nsew", padx=28, pady=(0, 6))

copyright_label = ctk.CTkLabel(
    self,
    text=COPYRIGHT_TEXT,
    font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
    text_color=GOOGLE_MATERIAL_PALETTE["on_surface_variant"],
)
copyright_label.grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 8))
```

- [ ] **Step 4: Run the focused test and full suite**

Run:

```powershell
python -m unittest tests.test_gui.GuiCommandTests.test_copyright_text_uses_approved_owner_and_year
python -m unittest discover
```

Expected: the focused test and the full suite both report `OK`.

- [ ] **Step 5: Inspect the real GUI at default and minimum sizes**

Run the GUI locally, confirm that the copyright line is centered below the white main panel, and resize the window to `860x620`. The text must remain complete and must not overlap the panel or operating-system window chrome.

