import re
from enum import Enum


class Merchant(Enum):
    GameStop = "GameStop"
    BestBuy = "Best Buy"
    HomeDepot = "The Home Depot"
    Nike = "Nike"
    Southwest = "Southwest Airlines"
    GuitarCenter = "Guitar Center"
    Starbucks = "Starbucks"
    StarbucksCA = "Starbucks Canada"


merchant_regex = {
    Merchant.GameStop: re.compile("^636491[0-9]{13}$"),
    # Merchant.Sears: re.compile('^[0-9]{16}$'), # TODO : refine
    # Merchant.Target: re.compile('^04(9|1)[0-9]{12}$'),
    # Merchant.Lowes: re.compile('^60064917[0-9]{11}$'),
    Merchant.BestBuy: re.compile("^(61|60)[0-9]{14}$"),
    Merchant.Nike: re.compile("^606010[0-9]{13}"),
    # Merchant.Lowes: TODO
    Merchant.HomeDepot: re.compile(r"^9\d{22}"),
    Merchant.GuitarCenter: re.compile("^61(53|64)[0-9]{12}"),
    Merchant.Starbucks: re.compile(r"^61\d{14}"),
    # Starbucks CA physical/eGift numbers are 16 digits (commonly 60/61 prefix)
    Merchant.StarbucksCA: re.compile(r"^6[01]\d{14}$"),
}


def GiftCardSchema(merchant):
    def merchant_check(field, value, error):
        if not merchant_regex[merchant].match(value):
            error(field, "invalid card number for merchant: {}".format(merchant))

    pin_schema = {
        "required": True,
        "type": "string",
        "minlength": 4,
        "maxlength": 8,
        "empty": False,
    }

    # Starbucks Canada security codes are always 8 digits.
    if merchant == Merchant.StarbucksCA:
        pin_schema["minlength"] = 8
        pin_schema["maxlength"] = 8
        pin_schema["regex"] = "^[0-9]{8}$"

    return {
        "card_number": {
            "required": True,
            "type": "string",
            "empty": False,
            "check_with": merchant_check,
        },
        "pin": pin_schema,
    }
