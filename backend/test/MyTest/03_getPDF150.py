from pathlib import Path

from pypdf import PdfReader, PdfWriter

base_path = Path(__file__).parent 

input_pdf = base_path / "CS2610DNW_Manuals_ALL_20240423095554.pdf"
output_pdf = base_path / "CS2610DNW_Manuals_ALL_20240423095554_2.pdf"

reader = PdfReader(input_pdf)
writer = PdfWriter()

# PDF 页码从 0 开始，所以前150页就是 0~149
for i in range(min(100, len(reader.pages))):
    writer.add_page(reader.pages[i])

with open(output_pdf, "wb") as f:
    writer.write(f)

print(f"已保存：{output_pdf}")