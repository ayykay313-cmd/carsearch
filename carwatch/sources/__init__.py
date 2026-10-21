from .alfuttaim import HondaPreowned, ToyotaPreowned
from .cars24 import Cars24
from .carswitch import CarSwitch
from .dubicars import DubiCars
from .kavak import Kavak

# name in config.yaml -> source class
SOURCES = {
    "toyota_preowned": ToyotaPreowned,
    "honda_preowned": HondaPreowned,
    "dubicars": DubiCars,
    "carswitch": CarSwitch,
    "cars24": Cars24,
    "kavak": Kavak,
}
