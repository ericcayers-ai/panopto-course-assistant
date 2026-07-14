from pathlib import Path
p = Path("app/schedule_parser.py")
text = p.read_text(encoding="utf-8")
needle = "def parse_notion_zip(source: Union[str, Path, bytes]) -> Dict[str, Any]:\n"
idx = text.find(needle)
if idx < 0:
    raise SystemExit("not found")
# find end of opening zip logic - replace until csv_names line block start
start = idx
old_block_end = text.find("    csv_names = [n for n in zf.namelist() if n.lower().endswith(\".csv\")]", start)
if old_block_end < 0:
    raise SystemExit("csv_names not found")
# include lines before csv_names that open zf
helper = '''def _open_notion_zip(source: Union[str, Path, bytes]) -> zipfile.ZipFile:
    """Open a Notion export zip, unwrapping a single nested Part-1 zip if needed."""
    if isinstance(source, bytes):
        raw = source
    else:
        raw = Path(source).read_bytes()
    zf = zipfile.ZipFile(io.BytesIO(raw))
    if any(n.lower().endswith(".csv") for n in zf.namelist()):
        return zf
    nested = [n for n in zf.namelist() if n.lower().endswith(".zip")]
    if len(nested) == 1:
        return zipfile.ZipFile(io.BytesIO(zf.read(nested[0])))
    return zf


def parse_notion_zip(source: Union[str, Path, bytes]) -> Dict[str, Any]:
    """Read a Notion export zip and return all parsed schedule tasks."""
    zf = _open_notion_zip(source)

'''
# remove old function header and zip open lines up to csv_names
old_func_start = start
# find after docstring and if/else block
marker = "    zf = _open_notion_zip(source)\n\n    csv_names"
# simpler: replace whole function opening
import re
pattern = re.compile(
    r"def parse_notion_zip\(source: Union\[str, Path, bytes\]\) -> Dict\[str, Any\]:\n"
    r"    \"\"\"Read a Notion export zip and return all parsed schedule tasks\.\"\"\"\n"
    r"    if isinstance\(source, bytes\):\n"
    r"        zf = zipfile\.ZipFile\(io\.BytesIO\(source\)\)\n"
    r"    else:\n"
    r"        zf = zipfile\.ZipFile\(Path\(source\)\)\n\n",
    re.M,
)
new_text, n = pattern.subn(helper, text, count=1)
if n != 1:
    raise SystemExit(f"replace count {n}")
p.write_text(new_text, encoding="utf-8")
print("ok", n)
