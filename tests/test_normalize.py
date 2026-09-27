"""Normalization contract tests: concrete token tuples, not just 'no error'."""

import pytest

from er_backend.corpus.normalize import Normalizer
from er_backend.errors import ERCategory, InputValidationError

from .fixtures import TOKEN_MAP


@pytest.fixture()
def normalizer() -> Normalizer:
    return Normalizer()


def test_fullwidth_and_casefold_normalize_to_ascii_tokens(normalizer):
    out = normalizer.normalize("ＡＣＭＥ Ｔｒａｄｉｎｇ ＬＴＤ")
    assert out.tokens == ("acme", "trading")
    assert out.normalized == "acme trading"


def test_latin_legal_suffixes_and_punctuation_stripped(normalizer):
    assert normalizer.normalize("Acme Trading Ltd.").tokens == ("acme", "trading")
    assert normalizer.normalize("ACME Trading, Inc.").tokens == ("acme", "trading")
    assert normalizer.normalize("Acme Trading GmbH").tokens == ("acme", "trading")


def test_cjk_legal_suffix_stripped_and_unigram_fallback(normalizer):
    # Without a token map, CJK content falls back to character unigrams.
    out = normalizer.normalize("北京星辰科技有限公司")
    assert out.tokens == ("北", "京", "星", "辰", "科", "技")


def test_cross_lingual_token_map_fixture():
    mapped = Normalizer(TOKEN_MAP).normalize("北京星辰科技有限公司")
    assert mapped.tokens == ("beijing", "xingchen", "technology")
    latin = Normalizer(TOKEN_MAP).normalize("Beijing Xingchen Technology Ltd")
    assert latin.tokens == mapped.tokens


def test_alias_token_map_replacement_is_multi_char_aware():
    norm = Normalizer({"星辰科技": "startech"})
    assert norm.normalize("北京星辰科技有限公司").tokens == ("北", "京", "startech")


def test_name_of_only_legal_tokens_is_an_input_error(normalizer):
    with pytest.raises(InputValidationError) as excinfo:
        normalizer.normalize("Ltd")
    assert excinfo.value.category == ERCategory.INPUT_ERROR


def test_empty_name_is_an_input_error(normalizer):
    with pytest.raises(InputValidationError):
        normalizer.normalize("   ")
