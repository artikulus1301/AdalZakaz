"""Ядро заказа: обычная программа без ИИ. Хранит и проверяет факты."""


class CoreError(Exception):
    """Нарушение правила ядра. status — HTTP-код для API."""

    def __init__(self, message: str, status: int = 400, code: str = "rule"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


class NotFound(CoreError):
    def __init__(self, what: str):
        super().__init__(f"{what} не найден", status=404, code="not_found")


class OutOfStock(CoreError):
    def __init__(self, sku: str, need: int, free: int):
        super().__init__(
            f"Недостаточно товара {sku}: нужно {need}, свободно {free}. Заказ не принят.",
            status=409,
            code="out_of_stock",
        )
