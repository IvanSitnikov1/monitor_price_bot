import asyncio
import logging
import shutil
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from playwright_stealth import Stealth

from utils.scraper import _get_system_profile, _start_chrome_manually


logger = logging.getLogger(__name__)


class BrowserInstance:
  """
  Экземпляр браузера для одного пользователя.
  """

  def __init__(self, user_id: int, port: int):
    self.user_id = user_id
    self.port = port
    self._playwright = None
    self._browser: Browser | None = None
    self._context: BrowserContext | None = None
    self._cart_page: Page | None = None
    self._chrome_process = None
    self._current_cart_url: str | None = None

  async def ensure_browser(self) -> tuple[Browser, BrowserContext]:
    """
    Гарантирует, что браузер запущен и подключен.

    Returns:
      Кортеж (browser, context)
    """
    if self._browser and self._context:
      logger.info(f"[User {self.user_id}] Используем существующий браузер на порту {self.port}")
      return self._browser, self._context

    # Определяем профиль
    user_profile = Path(f"user_data/{self.user_id}").absolute()

    # Если профиля пользователя нет — копируем системный профиль как шаблон
    if not user_profile.exists() or not (user_profile / "Default").exists():
      logger.info(f"[User {self.user_id}] Профиль не найден, создаём на основе системного")

      # Получаем системный профиль
      system_profile = _get_system_profile()

      if system_profile.exists():
        try:
          # Копируем системный профиль как шаблон
          logger.info(f"[User {self.user_id}] Копирование системного профиля: {system_profile} -> {user_profile}")
          shutil.copytree(system_profile, user_profile, dirs_exist_ok=True, ignore_dangling_symlinks=True)
          logger.info(f"[User {self.user_id}] Системный профиль успешно скопирован")
        except Exception as e:
          logger.warning(f"[User {self.user_id}] Не удалось скопировать системный профиль: {e}")
          logger.info(f"[User {self.user_id}] Создаём пустой профиль")
          user_profile.mkdir(parents=True, exist_ok=True)
      else:
        logger.warning(f"[User {self.user_id}] Системный профиль не найден, создаём пустой")
        user_profile.mkdir(parents=True, exist_ok=True)

    user_data_dir = user_profile
    logger.info(f"[User {self.user_id}] Используется профиль: {user_data_dir}")

    cdp_url = f"http://localhost:{self.port}"

    self._playwright = await async_playwright().start()
    logger.info(f"[User {self.user_id}] Попытка подключения к Chrome через CDP: {cdp_url}")

    try:
      # Пытаемся подключиться к уже запущенному Chrome
      self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
      logger.info(f"[User {self.user_id}] Успешно подключились к существующему Chrome")

      contexts = self._browser.contexts
      if contexts:
        self._context = contexts[0]
        logger.info(f"[User {self.user_id}] Используем существующий контекст, всего контекстов: {len(contexts)}")
      else:
        logger.error(f"[User {self.user_id}] Нет доступных контекстов в браузере")
        await self._browser.close()
        raise RuntimeError("Нет доступных контекстов")

    except Exception as e:
      logger.warning(f"[User {self.user_id}] Не удалось подключиться к Chrome через CDP: {e}")
      logger.info(f"[User {self.user_id}] Запускаем Chrome вручную через subprocess на порту {self.port}...")

      self._chrome_process = _start_chrome_manually(user_data_dir, port=self.port)
      if not self._chrome_process:
        raise RuntimeError("Не удалось запустить Chrome")

      logger.info(f"[User {self.user_id}] Ожидание запуска Chrome...")
      await asyncio.sleep(5)

      try:
        self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
        logger.info(f"[User {self.user_id}] Успешно подключились к Chrome, запущенному через subprocess")

        contexts = self._browser.contexts
        if contexts:
          self._context = contexts[0]
          logger.info(f"[User {self.user_id}] Используем контекст, всего контекстов: {len(contexts)}")
        else:
          logger.error(f"[User {self.user_id}] Нет доступных контекстов")
          await self._browser.close()
          if self._chrome_process:
            self._chrome_process.terminate()
          raise RuntimeError("Нет доступных контекстов")

      except Exception as e2:
        logger.error(f"[User {self.user_id}] Не удалось подключиться к Chrome после запуска: {e2}")
        if self._chrome_process:
          self._chrome_process.terminate()
        raise

    return self._browser, self._context

  async def get_cart_page(self, cart_url: str) -> Page:
    """
    Возвращает страницу корзины. При первом вызове открывает страницу,
    при последующих — переиспользует существующую.

    Args:
      cart_url: URL корзины

    Returns:
      Страница Playwright с открытой корзиной
    """
    browser, context = await self.ensure_browser()

    # Если страница уже открыта и URL совпадает — переиспользуем
    if self._cart_page and self._current_cart_url == cart_url:
      logger.info(f"[User {self.user_id}] Переиспользуем существующую страницу корзины")
      return self._cart_page

    # Закрываем старую страницу, если она была для другой корзины
    if self._cart_page:
      logger.info(f"[User {self.user_id}] Закрываем старую страницу корзины")
      await self._cart_page.close()
      self._cart_page = None

    # Закрываем все лишние вкладки, оставляем только одну
    if context.pages:
      page = context.pages[0]
      logger.info(f"[User {self.user_id}] Используем существующую страницу, всего страниц: {len(context.pages)}")
      for i, extra_page in enumerate(context.pages[1:], 1):
        await extra_page.close()
        logger.info(f"[User {self.user_id}] Закрыта лишняя вкладка {i}")
    else:
      page = await context.new_page()
      logger.info(f"[User {self.user_id}] Создана новая страница")

    # Применяем playwright-stealth
    stealth = Stealth(
      navigator_languages_override=('ru-RU', 'ru', 'en-US', 'en'),
      navigator_platform_override='Linux x86_64',
      navigator_user_agent_override='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36',
    )
    await stealth.apply_stealth_async(page)
    logger.info(f"[User {self.user_id}] Stealth-патчи применены")

    await page.set_viewport_size({"width": 1920, "height": 1080})

    # Переходим на страницу корзины
    logger.info(f"[User {self.user_id}] Первое открытие страницы корзины: {cart_url}")
    await page.goto(cart_url, timeout=45000, wait_until='domcontentloaded')
    logger.info(f"[User {self.user_id}] Страница корзины загружена")

    self._cart_page = page
    self._current_cart_url = cart_url

    return self._cart_page

  async def refresh_cart_page(self) -> Page:
    """
    Обновляет страницу корзины (имитирует F5).
    Должна быть вызвана после get_cart_page().

    Returns:
      Обновленная страница корзины
    """
    if not self._cart_page:
      raise RuntimeError("Страница корзины не открыта. Вызовите get_cart_page() сначала.")

    logger.info(f"[User {self.user_id}] Обновление страницы корзины (имитация F5)")
    await self._cart_page.reload(timeout=45000, wait_until='domcontentloaded')
    logger.info(f"[User {self.user_id}] Страница корзины обновлена")

    return self._cart_page


class BrowserManager:
  """
  Менеджер браузеров для нескольких пользователей.
  Каждый пользователь получает свой Chrome процесс на отдельном порту.
  """

  def __init__(self, base_port: int = 9222):
    self.base_port = base_port
    self._instances: dict[int, BrowserInstance] = {}
    self._port_counter = 0

  def _get_next_port(self) -> int:
    """Возвращает следующий свободный порт"""
    port = self.base_port + self._port_counter
    self._port_counter += 1
    return port

  def _get_instance(self, user_id: int) -> BrowserInstance:
    """
    Возвращает экземпляр браузера для пользователя.
    Создаёт новый, если не существует.

    Args:
      user_id: ID пользователя

    Returns:
      BrowserInstance для данного пользователя
    """
    if user_id not in self._instances:
      port = self._get_next_port()
      logger.info(f"Создание нового экземпляра браузера для user_id={user_id} на порту {port}")
      self._instances[user_id] = BrowserInstance(user_id, port)

    return self._instances[user_id]

  async def get_cart_page(self, cart_url: str, user_id: int) -> Page:
    """
    Возвращает страницу корзины для пользователя.

    Args:
      cart_url: URL корзины
      user_id: ID пользователя

    Returns:
      Страница Playwright с открытой корзиной
    """
    instance = self._get_instance(user_id)
    return await instance.get_cart_page(cart_url)

  async def refresh_cart_page(self, user_id: int) -> Page:
    """
    Обновляет страницу корзины для пользователя (имитирует F5).

    Args:
      user_id: ID пользователя

    Returns:
      Обновленная страница корзины
    """
    if user_id not in self._instances:
      raise RuntimeError(f"Нет активного браузера для user_id={user_id}. Вызовите get_cart_page() сначала.")

    instance = self._instances[user_id]
    return await instance.refresh_cart_page()

  async def close(self):
    """Закрывает все браузеры (не используется, браузеры остаются открытыми)"""
    logger.info("BrowserManager.close() вызван, но браузеры остаются открытыми")
    pass
