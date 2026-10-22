/** Страница /admin: переключатель режима конвейера. Доступ в prod закрывает oauth2-proxy. */
import { api } from "./api.js";

const $ = (id) => document.getElementById(id);
const radios = () => [...document.querySelectorAll('input[name="mode"]')];

function show(state) {
  for (const r of radios()) r.checked = r.value === state.mode;
  $("default").textContent = state.default;
}

async function load() {
  try {
    show(await api.getPipelineMode());
  } catch (err) {
    $("status").textContent = err.message === "HTTP 401" ? "нет доступа: войдите заново или проверьте API-ключ" : err.message;
  }
}

async function identify() {
  // /oauth2/userinfo существует только за oauth2-proxy; в dev-стеке запрос просто не пройдёт
  try {
    const response = await fetch("/oauth2/userinfo");
    if (!response.ok) return;
    const { email } = await response.json();
    $("user").textContent = email || "";
    $("link-logout").classList.remove("hidden");
    $("card-workflows").classList.remove("hidden");
  } catch { /* без прокси — просто не показываем пользователя */ }
}

$("btn-apply").addEventListener("click", async () => {
  const mode = radios().find((r) => r.checked)?.value;
  if (!mode) return;
  $("status").textContent = "применяю…";
  try {
    show(await api.setPipelineMode(mode));
    $("status").textContent = `готово: новые задачи пойдут в режиме ${mode}`;
  } catch (err) {
    $("status").textContent = err.message;
  }
});

identify();
load();
