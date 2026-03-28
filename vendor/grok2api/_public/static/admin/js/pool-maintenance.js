let apiKey = '';
let initialConfigLoaded = false;
let formDirty = false;
const SECRET_FIELDS = new Set([
  'temp_mail_admin_password',
  'temp_mail_site_password',
  'api_token',
]);

const byId = (id) => document.getElementById(id);
const DEFAULT_FALLBACK_PLACEHOLDER = '留空时自动跟随注册机默认值';

function markFormDirty() {
  if (!initialConfigLoaded) return;
  formDirty = true;
}

function bindFormDirtyTracking() {
  document.querySelectorAll('main input[id], main textarea[id], main select[id]').forEach((el) => {
    if (el.dataset.dirtyBound === '1') return;
    const eventName = el.type === 'checkbox' || el.tagName === 'SELECT' ? 'change' : 'input';
    el.addEventListener(eventName, markFormDirty);
    if (eventName !== 'change') {
      el.addEventListener('change', markFormDirty);
    }
    el.dataset.dirtyBound = '1';
  });
}

function resetDynamicPlaceholders() {
  document.querySelectorAll('[data-default-placeholder]').forEach((el) => {
    el.placeholder = el.dataset.defaultPlaceholder || '';
  });
}

function setInputValue(id, value) {
  const el = byId(id);
  if (!el) return;
  if (el.type === 'checkbox') {
    el.checked = Boolean(value);
  } else {
    el.value = value == null ? '' : String(value);
  }
}

function getInputValue(id) {
  const el = byId(id);
  if (!el) return '';
  return el.type === 'checkbox' ? el.checked : el.value.trim();
}

function formatTimestamp(value) {
  return value || '-';
}

function summarizeLastResult(result) {
  if (!result) return '暂无执行记录';
  return JSON.stringify(result, null, 2);
}

function maskSecret(value) {
  const text = value == null ? '' : String(value);
  if (!text) return '';
  if (text.length <= 8) return '*'.repeat(text.length);
  return `${text.slice(0, 2)}***${text.slice(-2)}`;
}

function sanitizeConfigForDisplay(data) {
  if (!data || typeof data !== 'object') return {};
  const sanitized = {};
  Object.entries(data).forEach(([key, value]) => {
    sanitized[key] = SECRET_FIELDS.has(key) ? maskSecret(value) : value;
  });
  return sanitized;
}

function setConsoleFallbackPlaceholders(consoleDefaults) {
  resetDynamicPlaceholders();
  const defaults = consoleDefaults || {};
  const directFields = [
    'proxy',
    'browser_proxy',
    'temp_mail_api_base',
    'temp_mail_domain',
    'api_endpoint',
  ];

  directFields.forEach((id) => {
    const el = byId(id);
    if (!el || el.type === 'checkbox') return;
    if ((el.value || '').trim()) return;
    const fallback = defaults[id];
    if (fallback) {
      el.placeholder = String(fallback);
    }
  });

  ['temp_mail_admin_password', 'temp_mail_site_password', 'api_token'].forEach((id) => {
    const el = byId(id);
    if (!el || el.type === 'checkbox') return;
    if ((el.value || '').trim()) return;
    if (defaults[id]) {
      el.placeholder = DEFAULT_FALLBACK_PLACEHOLDER;
    }
  });
}

function renderTasks(tasks) {
  const tbody = byId('task-table-body');
  if (!tbody) return;

  if (!tasks || tasks.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="text-center text-[var(--accents-4)] py-8">暂无自动创建任务</td></tr>';
    return;
  }

  tbody.innerHTML = tasks.map((task) => {
    const progress = `${task.completed_count || 0}/${task.target_count || 0}`;
    return `
      <tr>
        <td class="text-left font-mono text-xs">${task.id ?? '-'}</td>
        <td class="text-left">${task.name || '-'}</td>
        <td class="text-center">${task.status || '-'}</td>
        <td class="text-center">${progress}</td>
        <td class="text-left text-xs text-[var(--accents-4)]">${task.created_at || '-'}</td>
      </tr>
    `;
  }).join('');
}

function renderRuntime(data) {
  const runtime = data.runtime || {};
  const summary = runtime.token_summary || {};
  const pools = summary.pools || {};
  const removablePreview = runtime.removable_tokens || [];
  const lastResult = data.last_result || null;

  byId('stat-enabled').textContent = data.config?.enabled ? '已启用' : '未启用';
  byId('stat-last-run').textContent = `最近执行: ${formatTimestamp(data.last_run_finished_at)}`;
  byId('stat-active').textContent = String(summary.active_total ?? 0);
  byId('stat-total').textContent = `总号数: ${summary.total_tokens ?? 0}`;
  byId('stat-removable').textContent = String(summary.removable_total ?? 0);
  byId('stat-removable-detail').textContent = removablePreview.length
    ? removablePreview.slice(0, 3).map((item) => `${item.status}:${item.token_preview}`).join(' | ')
    : '暂无 cooling / expired 号';
  byId('stat-running-tasks').textContent = String(runtime.running_task_count ?? 0);
  byId('stat-console').textContent = runtime.console_error
    ? `Console 异常: ${runtime.console_error}`
    : 'Console 正常';
  byId('stat-last-result').textContent = lastResult
    ? `删 ${lastResult.removed_count || 0} / 补 ${lastResult.created_task_count || 0}`
    : '-';
  byId('stat-last-error').textContent = data.last_error || '最近无异常';
  byId('last-result-box').textContent = summarizeLastResult({
    last_result: lastResult,
    pools,
    console_error: runtime.console_error || '',
  });

  renderTasks(runtime.managed_tasks || []);
}

function renderConsoleDefaults(data) {
  const consoleDefaults = data.console_defaults || null;
  const effectiveConfig = data.effective_task_config || null;
  const consoleError = data.console_defaults_error || '';

  const metaEl = byId('console-defaults-meta');
  const defaultsBox = byId('console-defaults-box');
  const effectiveBox = byId('effective-config-box');

  if (metaEl) {
    if (consoleError) {
      metaEl.textContent = `读取注册机默认配置失败: ${consoleError}`;
    } else if (consoleDefaults) {
      metaEl.textContent = '已成功读取 console 当前默认配置。';
    } else {
      metaEl.textContent = '暂未读取到 console 默认配置。';
    }
  }

  if (defaultsBox) {
    defaultsBox.textContent = consoleDefaults
      ? JSON.stringify(sanitizeConfigForDisplay(consoleDefaults), null, 2)
      : (consoleError || '暂无默认配置');
  }

  if (effectiveBox) {
    effectiveBox.textContent = effectiveConfig
      ? JSON.stringify(sanitizeConfigForDisplay(effectiveConfig), null, 2)
      : '暂无有效配置';
  }

  setConsoleFallbackPlaceholders(consoleDefaults || {});
}

function applyConfig(config) {
  setInputValue('enabled', config.enabled);
  setInputValue('console_url', config.console_url);
  setInputValue('interval_sec', config.interval_sec);
  setInputValue('managed_pools', (config.managed_pools || []).join(','));
  setInputValue('min_active_tokens', config.min_active_tokens);
  setInputValue('register_count_per_task', config.register_count_per_task);
  setInputValue('max_running_tasks', config.max_running_tasks);
  setInputValue('task_name_prefix', config.task_name_prefix);
  setInputValue('delete_cooling_tokens', config.delete_cooling_tokens);
  setInputValue('delete_expired_tokens', config.delete_expired_tokens);
  setInputValue('proxy', config.proxy);
  setInputValue('browser_proxy', config.browser_proxy);
  setInputValue('temp_mail_api_base', config.temp_mail_api_base);
  setInputValue('temp_mail_admin_password', config.temp_mail_admin_password);
  setInputValue('temp_mail_domain', config.temp_mail_domain);
  setInputValue('temp_mail_site_password', config.temp_mail_site_password);
  setInputValue('api_endpoint', config.api_endpoint);
  setInputValue('api_token', config.api_token);
  setInputValue('api_append', config.api_append);
  setInputValue('api_auto_enable_nsfw', config.api_auto_enable_nsfw);
}

function collectPayload() {
  return {
    enabled: getInputValue('enabled'),
    console_url: getInputValue('console_url'),
    interval_sec: Number(getInputValue('interval_sec') || 60),
    managed_pools: getInputValue('managed_pools'),
    min_active_tokens: Number(getInputValue('min_active_tokens') || 1),
    register_count_per_task: Number(getInputValue('register_count_per_task') || 1),
    max_running_tasks: Number(getInputValue('max_running_tasks') || 1),
    task_name_prefix: getInputValue('task_name_prefix'),
    delete_cooling_tokens: getInputValue('delete_cooling_tokens'),
    delete_expired_tokens: getInputValue('delete_expired_tokens'),
    proxy: getInputValue('proxy'),
    browser_proxy: getInputValue('browser_proxy'),
    temp_mail_api_base: getInputValue('temp_mail_api_base'),
    temp_mail_admin_password: getInputValue('temp_mail_admin_password'),
    temp_mail_domain: getInputValue('temp_mail_domain'),
    temp_mail_site_password: getInputValue('temp_mail_site_password'),
    api_endpoint: getInputValue('api_endpoint'),
    api_token: getInputValue('api_token'),
    api_append: getInputValue('api_append'),
    api_auto_enable_nsfw: getInputValue('api_auto_enable_nsfw'),
  };
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (err) {
      throw new Error(`接口返回了无效 JSON: ${text.slice(0, 200)}`);
    }
  }
  if (!response.ok) {
    throw new Error(data?.detail || data?.message || `HTTP ${response.status}`);
  }
  return data;
}

async function loadPage(options = {}) {
  const { forceConfig = false } = options;
  try {
    const data = await requestJson('/v1/admin/pool-maintenance', {
      headers: buildAuthHeaders(apiKey),
    });
    if (forceConfig || !initialConfigLoaded || !formDirty) {
      applyConfig(data.config || {});
      initialConfigLoaded = true;
      formDirty = false;
    }
    renderRuntime(data);
    renderConsoleDefaults(data);
  } catch (err) {
    showToast(`加载号池维护失败: ${err.message}`, 'error');
  }
}

async function loadStatusOnly() {
  await loadPage();
}

async function saveSettings() {
  const btn = byId('save-btn');
  if (btn) btn.disabled = true;
  try {
    await requestJson('/v1/admin/pool-maintenance', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...buildAuthHeaders(apiKey),
      },
      body: JSON.stringify(collectPayload()),
    });
    showToast('号池维护配置已保存', 'success');
    formDirty = false;
    initialConfigLoaded = false;
    await loadPage({ forceConfig: true });
  } catch (err) {
    showToast(`保存失败: ${err.message}`, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function runNow() {
  const btn = byId('run-btn');
  if (btn) btn.disabled = true;
  try {
    const data = await requestJson('/v1/admin/pool-maintenance/run', {
      method: 'POST',
      headers: buildAuthHeaders(apiKey),
    });
    const result = data.result || {};
    showToast(`执行完成：删除 ${result.removed_count || 0} 个，补号任务 ${result.created_task_count || 0} 个`, 'success');
    await loadPage();
  } catch (err) {
    showToast(`执行失败: ${err.message}`, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function init() {
  apiKey = await ensureAdminKey();
  if (apiKey === null) return;
  bindFormDirtyTracking();
  await loadPage({ forceConfig: true });
  window.setInterval(() => {
    if (!document.hidden) {
      loadStatusOnly();
    }
  }, 15000);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
