import pytest

from app.config import BackgroundModel
from app.pwm import PWM
from tests.reference_data import M2_MATRIX, M2_PSEUDOCOUNT


@pytest.fixture()
def uniform_background() -> BackgroundModel:
    return BackgroundModel.uniform()


@pytest.fixture()
def m2_pwm(uniform_background) -> PWM:
    return PWM(
        matrix=[row[:] for row in M2_MATRIX],
        background=uniform_background,
        pseudocount=M2_PSEUDOCOUNT,
        name="M2",
    )
