"""Generates a sample sales spreadsheet for AEFlow Excel summation demo."""

import zipfile
from pathlib import Path


def create_sales_demo_xlsx(output_path: str = "examples/sales_demo.xlsx") -> None:
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""

    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

    wb = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="季度销售表" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>"""

    wb_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""

    sheet = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1">
      <c r="A1" t="inlineStr"><is><t>商品名称</t></is></c>
      <c r="B1" t="inlineStr"><is><t>单价(元)</t></is></c>
      <c r="C1" t="inlineStr"><is><t>数量</t></is></c>
      <c r="D1" t="inlineStr"><is><t>销售额(元)</t></is></c>
    </row>
    <row r="2">
      <c r="A2" t="inlineStr"><is><t>MacBook Pro 16</t></is></c>
      <c r="B2"><v>19999</v></c>
      <c r="C2"><v>2</v></c>
      <c r="D2"><v>39998</v></c>
    </row>
    <row r="3">
      <c r="A3" t="inlineStr"><is><t>iPhone 16 Pro</t></is></c>
      <c r="B3"><v>7999</v></c>
      <c r="C3"><v>5</v></c>
      <c r="D3"><v>39995</v></c>
    </row>
    <row r="4">
      <c r="A4" t="inlineStr"><is><t>iPad Pro 11</t></is></c>
      <c r="B4"><v>6799</v></c>
      <c r="C4"><v>3</v></c>
      <c r="D4"><v>20397</v></c>
    </row>
    <row r="5">
      <c r="A5" t="inlineStr"><is><t>AirPods Pro 2</t></is></c>
      <c r="B5"><v>1899</v></c>
      <c r="C5"><v>10</v></c>
      <c r="D5"><v>18990</v></c>
    </row>
    <row r="6">
      <c r="A6" t="inlineStr"><is><t>Apple Watch Ultra</t></is></c>
      <c r="B6"><v>6299</v></c>
      <c r="C6"><v>2</v></c>
      <c r="D6"><v>12598</v></c>
    </row>
    <row r="7">
      <c r="A7" t="inlineStr"><is><t>总计 (Total)</t></is></c>
      <c r="B7"/>
      <c r="C7"/>
      <c r="D7"/>
    </row>
  </sheetData>
</worksheet>"""

    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)

    print(f"✅ Generated sales demo spreadsheet: {p.resolve()}")


if __name__ == "__main__":
    create_sales_demo_xlsx()
