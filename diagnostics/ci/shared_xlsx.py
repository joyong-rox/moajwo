"""공유 문자열(sharedStrings) xlsx를 직접 만든다 — Excel이 저장하는 방식 (openpyxl은 칸마다 글자를 넣어 저장한다).
같은 긴 글자를 N칸이 가리켜도 파일·풀었을 때 크기는 작다. 사용: shared_xlsx(path, rows, header)."""

import zipfile
from xml.sax.saxutils import escape

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "application/vnd.openxmlformats-officedocument.spreadsheetml"


def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def shared_xlsx(path: str, rows: list[list[str]], sheet: str = "s", merges: tuple[str, ...] = ()) -> None:
    strings: dict[str, int] = {}
    body = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            idx = strings.setdefault(value, len(strings))
            cells.append(f'<c r="{_col(c)}{r}" t="s"><v>{idx}</v></c>')
        body.append(f'<row r="{r}">{"".join(cells)}</row>')
    refs = "".join(f'<mergeCell ref="{m}"/>' for m in merges)
    merge = f'<mergeCells count="{len(merges)}">{refs}</mergeCells>' if merges else ""
    sheet_xml = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{NS}"><sheetData>{"".join(body)}</sheetData>{merge}</worksheet>'
    count = sum(len(r) for r in rows)
    sst = "".join(f'<si><t xml:space="preserve">{escape(s)}</t></si>' for s in strings)
    sst_xml = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><sst xmlns="{NS}" count="{count}" uniqueCount="{len(strings)}">{sst}</sst>'
    types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/xl/workbook.xml" ContentType="{CT}.sheet.main+xml"/>'
        f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{CT}.worksheet+xml"/>'
        f'<Override PartName="/xl/sharedStrings.xml" ContentType="{CT}.sharedStrings+xml"/>'
        "</Types>"
    )
    root_rels = f'<Relationships xmlns="{PKG}"><Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    workbook = (
        f'<workbook xmlns="{NS}" xmlns:r="{REL}"><sheets>'
        f'<sheet name="{escape(sheet)}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    wb_rels = (
        f'<Relationships xmlns="{PKG}">'
        f'<Relationship Id="rId1" Type="{REL}/worksheet" Target="worksheets/sheet1.xml"/>'
        f'<Relationship Id="rId2" Type="{REL}/sharedStrings" Target="sharedStrings.xml"/></Relationships>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        z.writestr("xl/sharedStrings.xml", sst_xml)
