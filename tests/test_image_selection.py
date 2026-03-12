import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from image_selection import select_ad_images


# Shared setup: available files for SKU = "XPTO"
BASE_FILES = [
    "XPTO-01.jpg",
    "XPTO-02.jpg",
    "XPTO-03.png",
    "XPTO-04.webp",
    "XPTOCB2-01.jpg",
    "XPTOCB2-02.png",
    "XPTO-CB3-01.jpg",
    "XPTOCB4-001.jpg",
    "relatorio.pdf",
    "OUTRO-SKU-01.jpg",
]


def _filenames(result):
    """Extract just the filenames from select_ad_images result."""
    return [r["fileName"] for r in result]


# Teste 1 — Anuncio simples
def test_simple_ad():
    result = select_ad_images(sku="XPTO", ad_type="simple", available_files=BASE_FILES)
    assert _filenames(result) == ["XPTO-01.jpg", "XPTO-02.jpg", "XPTO-03.png", "XPTO-04.webp"]
    assert all(r["source"] == "simple" for r in result)
    assert [r["position"] for r in result] == [1, 2, 3, 4]


# Teste 2 — Kit com 2
def test_kit_2():
    result = select_ad_images(sku="XPTO", ad_type="kit", kit_size=2, available_files=BASE_FILES)
    assert _filenames(result) == ["XPTOCB2-01.jpg", "XPTOCB2-02.png", "XPTO-03.png", "XPTO-04.webp"]
    assert result[0]["source"] == "kit"
    assert result[1]["source"] == "kit"
    assert result[2]["source"] == "simple"
    assert result[3]["source"] == "simple"


# Teste 3 — Kit com 3
def test_kit_3():
    result = select_ad_images(sku="XPTO", ad_type="kit", kit_size=3, available_files=BASE_FILES)
    assert _filenames(result) == ["XPTO-CB3-01.jpg", "XPTO-02.jpg", "XPTO-03.png", "XPTO-04.webp"]
    assert result[0]["source"] == "kit"


# Teste 4 — Kit com 4
def test_kit_4():
    result = select_ad_images(sku="XPTO", ad_type="kit", kit_size=4, available_files=BASE_FILES)
    assert _filenames(result) == ["XPTOCB4-001.jpg", "XPTO-02.jpg", "XPTO-03.png", "XPTO-04.webp"]
    assert result[0]["source"] == "kit"


# Teste 5 — Kit com 5 (sem imagens especificas, fallback para simples)
def test_kit_5_fallback():
    result = select_ad_images(sku="XPTO", ad_type="kit", kit_size=5, available_files=BASE_FILES)
    assert _filenames(result) == ["XPTO-01.jpg", "XPTO-02.jpg", "XPTO-03.png", "XPTO-04.webp"]
    assert all(r["source"] == "simple" for r in result)


# Teste 6 — Kit com imagem em posicao inexistente no simples (APPEND)
def test_kit_2_append_position():
    files = BASE_FILES + ["XPTOCB2-09.jpg"]
    result = select_ad_images(sku="XPTO", ad_type="kit", kit_size=2, available_files=files)
    assert _filenames(result) == [
        "XPTOCB2-01.jpg", "XPTOCB2-02.png", "XPTO-03.png", "XPTO-04.webp", "XPTOCB2-09.jpg"
    ]
    assert len(result) == 5
    assert result[4]["source"] == "kit"


# Teste 7 — Sequencia com furos (ordenacao numerica, nao alfabetica)
def test_numeric_ordering_with_gaps():
    files = ["XPTO-01.jpg", "XPTO-02.jpg", "XPTO-05.jpg", "XPTO-09.jpg", "XPTO-10.jpg", "XPTO-100.jpg"]
    result = select_ad_images(sku="XPTO", ad_type="simple", available_files=files)
    assert _filenames(result) == [
        "XPTO-01.jpg", "XPTO-02.jpg", "XPTO-05.jpg", "XPTO-09.jpg", "XPTO-10.jpg", "XPTO-100.jpg"
    ]


# Teste 8 — SKU com hifens (ex: "AB-CD-123")
def test_sku_with_hyphens():
    files = ["AB-CD-123-01.jpg", "AB-CD-123-02.jpg", "AB-CD-123CB2-01.jpg"]
    result = select_ad_images(sku="AB-CD-123", ad_type="kit", kit_size=2, available_files=files)
    assert _filenames(result) == ["AB-CD-123CB2-01.jpg", "AB-CD-123-02.jpg"]


# Teste 9 — Case insensitive para CB e SKU
def test_case_insensitive():
    files = ["xptocb2-01.jpg", "XPTOCb2-02.jpg"]
    result = select_ad_images(sku="XPTO", ad_type="kit", kit_size=2, available_files=files)
    assert len(result) == 2
    assert _filenames(result) == ["xptocb2-01.jpg", "XPTOCb2-02.jpg"]
    assert all(r["source"] == "kit" for r in result)


# Teste 10 — Sem nenhuma imagem relevante
def test_no_relevant_images():
    files = ["relatorio.pdf", "OUTRO-01.jpg"]
    result = select_ad_images(sku="XPTO", ad_type="simple", available_files=files)
    assert result == []


# Teste 11 — Sem imagens simples, com imagens de kit -> anuncio SIMPLES
def test_simple_ad_ignores_kit_images():
    files = ["XPTOCB2-01.jpg", "XPTOCB2-02.jpg"]
    result = select_ad_images(sku="XPTO", ad_type="simple", available_files=files)
    assert result == []


# Teste 12 — Sem imagens simples, com imagens de kit -> anuncio KIT
def test_kit_ad_without_simple_images():
    files = ["XPTOCB2-01.jpg", "XPTOCB2-02.jpg"]
    result = select_ad_images(sku="XPTO", ad_type="kit", kit_size=2, available_files=files)
    assert _filenames(result) == ["XPTOCB2-01.jpg", "XPTOCB2-02.jpg"]
    assert all(r["source"] == "kit" for r in result)


# Teste 13 — Qualquer extensao aceita
def test_any_extension():
    files = ["XPTO-01.tiff", "XPTO-02.bmp", "XPTO-03.gif", "XPTO-04.svg"]
    result = select_ad_images(sku="XPTO", ad_type="simple", available_files=files)
    assert _filenames(result) == ["XPTO-01.tiff", "XPTO-02.bmp", "XPTO-03.gif", "XPTO-04.svg"]


# Teste 14 — Zero-padding normalizado (colisao de posicao)
def test_zero_padding_collision():
    files = ["XPTO-1.jpg", "XPTO-01.png", "XPTO-001.webp"]
    result = select_ad_images(sku="XPTO", ad_type="simple", available_files=files)
    assert len(result) == 1
    assert result[0]["fileName"] == "XPTO-1.jpg"  # first in input list wins
    assert result[0]["position"] == 1


# Teste 15 — Simples sem dash entre SKU e sequencia
def test_simple_no_dash():
    files = ["XPTO01.jpg", "XPTO02.png", "XPTO03.webp", "XPTOCB201.jpg"]
    result = select_ad_images(sku="XPTO", ad_type="simple", available_files=files)
    assert _filenames(result) == ["XPTO01.jpg", "XPTO02.png", "XPTO03.webp"]


# Teste 16 — Kit sem dash entre SKU e sequencia
def test_kit_no_dash():
    files = ["XPTO01.jpg", "XPTO02.png", "XPTO03.webp", "XPTOCB201.jpg"]
    result = select_ad_images(sku="XPTO", ad_type="kit", kit_size=2, available_files=files)
    assert _filenames(result) == ["XPTOCB201.jpg", "XPTO02.png", "XPTO03.webp"]
    assert result[0]["source"] == "kit"
