from bili_recipe_notes.utils import build_output_folder_name, sanitize_filename, sec_to_timestamp


def test_sec_to_timestamp() -> None:
    assert sec_to_timestamp(0) == "00:00:00"
    assert sec_to_timestamp(83.9) == "00:01:23"
    assert sec_to_timestamp(3661) == "01:01:01"


def test_sanitize_filename() -> None:
    assert sanitize_filename("a/b:c*?") == "a_b_c__"
    assert sanitize_filename("   ") == "untitled"


def test_build_output_folder_name() -> None:
    assert build_output_folder_name("番茄炒蛋!!!", "UP主@厨房") == "番茄炒蛋 - UP主厨房"
    assert build_output_folder_name("***", None) == "unknown"
