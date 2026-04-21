from pathlib import Path
from unittest.mock import MagicMock


# Mocking the _extract_candidate method logic from find_magazine.py
def is_likely_english(filename, caption):
    return True  # Simulate english detection


def extract_candidate(msg, channel_name, channel_id, filename, caption=""):
    ext = Path(filename).suffix.lower()
    valid_exts = {".pdf", ".epub", ".mobi", ".zip", ".rar"}

    # Relaxed logic: If it's a PDF/EPUB, it's likely a candidate even without explicit "magazine" keywords
    is_potential_magazine = ext in valid_exts

    if is_potential_magazine:
        if not is_likely_english(filename, caption):
            return None

        return {"filename": filename, "caption": caption, "is_match": True}
    return None


def test():
    test_cases = [
        ("PC Pro.pdf", ""),
        ("MacWorld.pdf", ""),
        ("SomeRandomFile.pdf", ""),
        ("Installer.exe", ""),  # Should fail
        ("Magazine Issue 1.pdf", ""),
        ("National Geographic.epub", ""),
    ]

    print(f"{'Filename':<30} | {'Status':<10}")
    print("-" * 45)

    for filename, caption in test_cases:
        result = extract_candidate(MagicMock(), "Test Channel", 123, filename, caption)
        status = "MATCH" if result else "SKIP"
        print(f"{filename:<30} | {status:<10}")


if __name__ == "__main__":
    test()
