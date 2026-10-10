(function () {
  'use strict';
  var state = { items: [], active: null, main: document.getElementById('main'), input: document.getElementById('inputLayer'), cards: document.getElementById('cards') };
  var root = document.getElementById('root');
  var splitter = document.getElementById('splitter'), dragging = false;
  var clipboardRequestId = 0, pendingClipboardRequestId = '', pendingKeyboardPaste = null;
  // 主画面宽度只由这一处裁决：两列之间的分隔条写入这个值。
  // 列间距与页面内边距不属于列宽，右侧云机画面区域始终保留最小宽度。
  var MIN_MAIN_WIDTH = 260, MIN_SIDE_WIDTH = 220, DEFAULT_MAIN_WIDTH = 320, MAIN_WIDTH_STEP = 10;
  // 主画面底部那条快捷操作栏的高度：算主画面外框高度时和排版时要用同一个值。
  var MAIN_RAIL_HEIGHT = 48;
  var MAIN_WIDTH_KEY = 'myt.projection.mainWidth';
  // 画面大小只管右侧那些云机小画面：主画面始终吃掉剩下的空间，不再单独调节。
  // 拖动条在主界面那一排工具栏里；页面只保留唯一写入点，并把值存在自己的 localStorage。
  var MINI_SIZE_MIN = 90, MINI_SIZE_MAX = 260, MINI_SIZE_STEP = 10, DEFAULT_MINI_SIZE = 200;
  // 老版本会把拖到最左边的最小档记下来，之后每次打开都以最小档出现；换键名让新的默认值生效。
  var MINI_SIZE_KEY = 'myt.projection.miniSize.v2';
  var currentMiniSize = DEFAULT_MINI_SIZE;
  var dashboardStatus = document.getElementById('dashboardStatus');
  var mainControls = document.getElementById('mainControls');
  var mainName = document.getElementById('mainName');
  var syncBadge = document.getElementById('syncBadge');
  // 同步控制：只有“同步源”那一屏上的动作会重放给勾选的其它云机，切到别的画面只操作那一台。
  // 目标集合和同步源只有一个来源——宿主算好推过来；页面只负责显示，自己不再算一遍
  // （两处各算一套必然会对不上）。
  var syncState = { enabled: false, ids: [], sourceId: '', sourceName: '' };
  function isSyncTarget(id) { return syncState.enabled && syncState.ids.indexOf(String(id)) >= 0; }
  function isSyncSource(id) { return syncState.enabled && !!syncState.sourceId && String(id) === syncState.sourceId; }
  function sourceLabel() { return syncState.sourceName || syncState.sourceId; }
  // 当前这一屏上的操作会不会广播：开着同步、有源、正看着源那台，而且至少有一台要跟随。
  function broadcastsFromActive() {
    return syncState.enabled && !!syncState.sourceId && syncState.ids.length > 0 &&
      state.active === syncState.sourceId;
  }
  function updateSyncBadge() {
    if (!syncBadge) return;
    // 没有文字就整块隐藏：没开同步时不留占位。
    if (!syncState.enabled) {
      syncBadge.textContent = '';
      syncBadge.classList.remove('action');
      syncBadge.title = '';
      return;
    }
    // 文案要短：主画面最窄时这一行还要给云机名留出位置（完整说明在工具栏的开关提示里）。
    var text, actionable = false;
    if (!syncState.sourceId) text = '同步：先点开一台作为源';
    else if (!syncState.ids.length) text = '同步：先勾选要跟随的云机';
    else if (!state.active) text = '同步源 ' + sourceLabel();
    else if (state.active === syncState.sourceId) text = '同步源 · ' + syncState.ids.length + ' 台跟随';
    else {
      // 在非源画面上：这里只操作这一台，给一个一键回到源的动作。
      text = '本机单独操作 · 回到 ' + sourceLabel();
      actionable = true;
    }
    syncBadge.textContent = text;
    syncBadge.classList.toggle('action', actionable);
    syncBadge.title = actionable ? '点一下把主画面切回同步源，之后的操作才会一起发给其它云机' : '';
  }
  function applySyncMarks() {
    state.items.forEach(function (item) {
      if (!item || !item.card) return;
      var source = isSyncSource(item.id);
      var follower = !source && isSyncTarget(item.id);
      item.card.classList.toggle('sync-source', source);
      item.card.classList.toggle('sync-target', follower);
      // 标记要能解释自己：鼠标停上去就说清这台是源、还是会跟着源一起动。
      if (item.card.setAttribute) {
        var label = item.detail || item.name || item.id;
        item.card.setAttribute('title', source
          ? label + '（同步源：这台上的操作会一起发给勾选的云机）'
          : follower ? label + '（会跟随同步源的操作）' : label);
      }
    });
  }
  // 宿主是同步状态的唯一写入点（和画面大小同一种做法）。
  window.__dashboardSync = function (enabled, ids, sourceId, sourceName) {
    syncState.enabled = enabled === true;
    syncState.ids = (enabled === true && ids ? ids : []).map(function (value) {
      return String(value == null ? '' : value);
    }).filter(function (value) { return !!value; });
    syncState.sourceId = enabled === true ? String(sourceId == null ? '' : sourceId) : '';
    syncState.sourceName = enabled === true ? String(sourceName == null ? '' : sourceName) : '';
    updateSyncBadge();
    applySyncMarks();
  };
  // 点徽标＝把主画面切回同步源：不需要新的消息，本来就有一条“切画面”的通道。
  if (syncBadge) syncBadge.addEventListener('click', function () {
    if (!syncState.enabled || !syncState.sourceId || state.active === syncState.sourceId) return;
    if (!findItem(syncState.sourceId)) return;
    select(syncState.sourceId);
  });
  // 工作台被宿主收起来时下发暂停：页面不再给云机截图、也不再推动重连。
  var paused = false;
  function esc(value) { return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) { return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]; }); }
  function post(value) { if (window.chrome && window.chrome.webview) window.chrome.webview.postMessage(JSON.stringify(value)); }
  // 勾选只有列表里的行状态一个事实来源：宿主改了行状态就推过来，页面照着显示。
  function setCardSelected(item, selected) {
    if (!item) return;
    item.selected = !!selected;
    var pick = item.card && item.card.querySelector('.cardPick');
    if (pick) pick.checked = !!selected;
  }
  document.querySelectorAll('#mainControls button[data-command]').forEach(function (button) {
    button.addEventListener('click', function () { post({ type: 'navigate', command: button.getAttribute('data-command') }); });
  });
  var STATUS_AUTO_HIDE_MS = 4000, PENDING_STATUS_MS = 8000, PENDING_STATUS_TEXT = '正在准备投屏…';
  var statusTimer = 0, statusSeq = 0;
  function setStatus(value) { if (dashboardStatus) dashboardStatus.textContent = value || ''; }
  // 宿主（返回/主页/任务）下发的提示同样是一次性提示，不能一直挂在画面上。
  window.__dashboardStatus = function (value) { flashStatus(value); };
  // 提示条只在有事发生时出现，并自动收掉。页面打开时不留占位文字：那会让“正在准备投屏…”
  // 永远挂在主画面左下角，看起来像卡住了。
  function clearStatus() {
    statusSeq++;
    if (statusTimer) { window.clearTimeout(statusTimer); statusTimer = 0; }
    setStatus('');
  }
  function flashStatus(value, ms) {
    statusSeq++;
    if (statusTimer) { window.clearTimeout(statusTimer); statusTimer = 0; }
    setStatus(value);
    if (!value) return;
    statusTimer = window.setTimeout(function () { statusTimer = 0; setStatus(''); }, ms || STATUS_AUTO_HIDE_MS);
  }
  // 只有真的在等主画面上线时才显示“正在准备投屏…”，到点还没连上也要收掉。
  function showPendingStatus() {
    var seq = ++statusSeq;
    if (statusTimer) { window.clearTimeout(statusTimer); statusTimer = 0; }
    setStatus(PENDING_STATUS_TEXT);
    statusTimer = window.setTimeout(function () {
      statusTimer = 0;
      if (seq === statusSeq) setStatus('');
    }, PENDING_STATUS_MS);
  }
  // 文字只走画面自己的输入通道：预览画面内就是投屏页，粘贴和打字都交给它已经修好的
  // UTF-8 分包与发送队列，避免这里再实现一套编码规则导致中文、表情或换行出现乱码。
  function activeItem() { return state.items.filter(function (x) { return x.id === state.active; })[0] || null; }
  // 主画面必须自报身份：自动选中和“快捷操作发给哪台”都靠它，不能只靠缩略图高亮。
  function updateMainName() {
    if (!mainName) return;
    var item = activeItem();
    // 主画面这一条比缩略图宽，显示名称即可；完整身份（主机、坑位）放悬停提示。
    mainName.textContent = item ? (item.name || item.id) : '';
    mainName.title = item ? (item.detail || item.name || item.id) : '';
    mainName.style.display = item ? '' : 'none';
  }
  function findItem(id) {
    for (var index = 0; index < state.items.length; index++) {
      if (state.items[index] && state.items[index].id === id) return state.items[index];
    }
    return null;
  }
  function activePlayer(item) {
    var frame = item && item.live;
    if (!frame) return null;
    try {
      var player = frame.contentWindow && frame.contentWindow.h5_lgair;
      return player && typeof player.send_input_txt === 'function' ? player : null;
    } catch (_) { return null; }
  }
  /**
   * 把文字送进当前主画面的那台云机。
   *
   * @param pasted 来自粘贴（成功提示说"已粘贴到"，否则"已发送到"）。
   * @param silent 直接打字时用：一批字符攒起来发，成功提示不刷屏（失败提示照旧要给）。
   */
  function sendTextToActive(value, pasted, silent) {
    var text = value == null ? '' : String(value);
    if (!text) return false;
    var item = activeItem();
    if (!item) { flashStatus('请先点选一台云机画面，再输入文字。'); return false; }
    var player = activePlayer(item);
    if (!player) { flashStatus('云机画面还没准备好，请等画面出现后再输入文字。'); return false; }
    try {
      player.send_input_txt(text);
    } catch (error) {
      flashStatus('文字发送失败，请重新连接画面后再试。');
      return false;
    }
    // 同步控制开着时，同一份文字再发给勾选的其它云机：主画面走播放器这条通道，
    // 其它云机交给宿主（它们在预览里只是静帧，没有播放器可以送进去）。只有同步源那一屏才广播。
    if (broadcastsFromActive()) post({ type: 'text_sync', text: text });
    if (!silent) flashStatus((pasted ? '已粘贴到 ' : '已发送到 ') + (item.name || item.id));
    return true;
  }
  function hasHostClipboardBridge() {
    return !!(window.chrome && window.chrome.webview &&
      typeof window.chrome.webview.postMessage === 'function');
  }
  // 工作台是 http 虚拟主机页面，读不到 navigator.clipboard，粘贴只能由宿主窗口
  // 读 Windows 剪贴板后回传；超时后给出可执行的替代做法，不静默失败。
  function requestHostClipboard() {
    if (!hasHostClipboardBridge()) {
      flashStatus('当前环境读不到剪贴板。');
      return;
    }
    var requestId = 'dashboard-clipboard-' + (++clipboardRequestId);
    pendingClipboardRequestId = requestId;
    post({ type: 'clipboard_request', requestId: requestId });
    window.setTimeout(function () {
      if (pendingClipboardRequestId !== requestId) return;
      pendingClipboardRequestId = '';
      flashStatus('读取剪贴板超时，请再粘贴一次。');
    }, 2000);
  }
  function completeHostClipboard(message) {
    var requestId = String((message && message.requestId) || '');
    if (!pendingClipboardRequestId || requestId !== pendingClipboardRequestId) return;
    pendingClipboardRequestId = '';
    if (!message.succeeded) { flashStatus('读取剪贴板失败，请再粘贴一次。'); return; }
    var text = message.text == null ? '' : String(message.text);
    if (!text) { flashStatus('剪贴板里没有文字。'); return; }
    sendTextToActive(text, true);
  }
  document.addEventListener('paste', function (event) {
    if (pendingKeyboardPaste) { pendingKeyboardPaste.cancelled = true; pendingKeyboardPaste = null; }
    var data = event.clipboardData;
    var value = data && typeof data.getData === 'function' ? data.getData('text/plain') : '';
    if (!value && !hasHostClipboardBridge()) return;
    event.preventDefault();
    event.stopPropagation();
    if (value) sendTextToActive(value, true);
    else requestHostClipboard();
  }, true);
  // 回车/退格/删除/方向键**不能当文字发**：它们在云机上不是可打印字符，混在文字里发过去收不到
  // （用户实测：退格/回车按了没反应）。这些键走播放器自己的按键通道。
  var KEY_COMMANDS = {
    Enter: 'VK_RETURN', Backspace: 'VK_BACK', Delete: 'VK_DELETE',
    ArrowUp: 'VK_UP', ArrowDown: 'VK_DOWN', ArrowLeft: 'VK_LEFT', ArrowRight: 'VK_RIGHT'
  };
  function sendKeyToActive(command) {
    var item = activeItem();
    if (!item) { flashStatus('请先点选一台云机画面，再操作键盘。'); return false; }
    var player = activePlayer(item);
    if (!player || typeof player.sendCmdEvent !== 'function') {
      flashStatus('云机画面还没准备好，请等画面出现后再操作。');
      return false;
    }
    try { player.sendCmdEvent(command); }
    catch (error) { flashStatus('按键发送失败，请重新连接画面后再试。'); return false; }
    return true;
  }
  document.addEventListener('keydown', function (event) {
    if (!event.ctrlKey) return;
    var key = String(event.key || '').toLowerCase();
    if (key !== 'v' && event.keyCode !== 86 && event.which !== 86) return;
    if (!hasHostClipboardBridge()) return;
    event.preventDefault();
    event.stopPropagation();
    // 先占住这次粘贴，下一个事件循环再向宿主取剪贴板：浏览器自己派发 paste 事件时
    // 由上面的处理负责，避免同一次 Ctrl+V 发送两遍。
    if (pendingKeyboardPaste) pendingKeyboardPaste.cancelled = true;
    var request = { cancelled: false };
    pendingKeyboardPaste = request;
    window.setTimeout(function () {
      if (request.cancelled) return;
      if (pendingKeyboardPaste === request) pendingKeyboardPaste = null;
      requestHostClipboard();
    }, 0);
  }, true);
  // 直接打字：点一下主画面之后键盘就进云机（和独立投屏窗口一样）。
  // 为什么不做输入框：工作台的设计就是"画面直接接操作"，多一个输入框反而多一步。
  // 攒 TYPING_FLUSH_MS 再发一次：逐字符发会把通道刷爆、输入法也会被打乱；
  // 回车/退格按控制字符发（\n / \b，与独立窗口同一套约定）。
  var TYPING_FLUSH_MS = 120, typingBuffer = '', typingTimer = 0;
  function flushTyping() {
    if (typingTimer) { window.clearTimeout(typingTimer); typingTimer = 0; }
    var text = typingBuffer;
    typingBuffer = '';
    if (text) sendTextToActive(text, false, true);
  }
  function scheduleTypingFlush() {
    if (typingTimer) return;
    typingTimer = window.setTimeout(flushTyping, TYPING_FLUSH_MS);
  }
  // 中文输入法需要一个"能落字的输入区"才会开始组合。页面里没有可编辑元素时，系统输入法
  // 根本不进入组合状态，键盘只能拿到英文原字符——中文一个字都进不来（用户实测：预览里
  // 中文输不进去，像是被强制成英文）。所以这里挂一个看不见的输入区：点卡片/点主画面时
  // 聚焦它，输入法把整段中文提交到它的 compositionend 上，再按同一套攒批发进云机。
  // 它自己不留内容（每次取完就清空），也不参与布局。
  var typingTarget = null, typingComposing = false;
  function ensureTypingTarget() {
    if (typingTarget) return typingTarget;
    var node = document.createElement('textarea');
    node.id = '__dashboardTypingTarget';
    node.setAttribute('autocomplete', 'off');
    node.setAttribute('autocorrect', 'off');
    node.setAttribute('autocapitalize', 'off');
    node.setAttribute('spellcheck', 'false');
    node.setAttribute('aria-hidden', 'true');
    node.tabIndex = -1;
    // 1×1 透明、跟着光标走：输入法候选框会贴着它出现，既不挡画面也不参与布局，
    // 也不能吃掉点击（它就摆在用户刚点过的地方）。
    node.style.cssText = 'position:fixed;left:0;top:0;width:1px;height:1px;padding:0;margin:0;' +
      'border:0;opacity:0;resize:none;overflow:hidden;z-index:-1;pointer-events:none;caret-color:transparent;';
    node.addEventListener('compositionstart', function () { typingComposing = true; });
    node.addEventListener('compositionend', function () {
      typingComposing = false;
      // 提交完就清空：这段文字已经由 document 上那条 compositionend 收下并排队发送。
      node.value = '';
    });
    // 直接打字（英文/数字）走 input；输入法提交（insertCompositionText）不在这里发，
    // 否则同一段中文会发两遍。粘贴交给宿主剪贴板那条通道，这里也不重复发。
    node.addEventListener('input', function (event) {
      var type = String((event && event.inputType) || '');
      if (typingComposing || type.indexOf('Composition') >= 0) return;
      var text = event && typeof event.data === 'string' ? event.data : '';
      node.value = '';
      if (!text) return;
      if (type.indexOf('insertFromPaste') === 0 || type.indexOf('insertFromDrop') === 0) return;
      typingBuffer += text;
      scheduleTypingFlush();
    });
    if (document.body && typeof document.body.appendChild === 'function') document.body.appendChild(node);
    typingTarget = node;
    return node;
  }
  /** 把输入区挪到光标处并聚焦（输入法候选框跟着它出现）。没选云机时不抢键盘。 */
  function focusTypingTarget(x, y) {
    if (!state.active) return;
    var node = ensureTypingTarget();
    if (typeof x === 'number' && typeof y === 'number') {
      var maxX = Math.max(0, (window.innerWidth || 0) - 2);
      var maxY = Math.max(0, (window.innerHeight || 0) - 2);
      node.style.left = Math.max(0, Math.min(maxX, x)) + 'px';
      node.style.top = Math.max(0, Math.min(maxY, y)) + 'px';
    }
    node.value = '';
    try { node.focus({ preventScroll: true }); }
    catch (error) { try { node.focus(); } catch (ignored) { } }
  }
  function blurTypingTarget() {
    if (!typingTarget) return;
    typingTarget.value = '';
    try { typingTarget.blur(); } catch (error) { }
  }
  /** 这次按键要不要变成文字送进云机；控制键（方向键、Esc、F1…）一律不碰。 */
  function typeText(event) {
    var key = event && event.key;
    if (typeof key !== 'string' || key.length !== 1) return '';
    return key >= ' ' && key !== '\u007f' ? key : '';
  }
  document.addEventListener('keydown', function (event) {
    // 带 Ctrl/Alt/Win 的组合键属于客户端自己的快捷键（Ctrl+V 由上面那条处理），不抢。
    if (event.ctrlKey || event.altKey || event.metaKey) return;
    if (event.isComposing) return;   // 输入法还在组合：交给 compositionend 一次性给
    if (!state.active) return;       // 没选中云机时不抢键盘（免得打字打到别处）
    var command = KEY_COMMANDS[event.key];
    if (command) {
      event.preventDefault();
      event.stopPropagation();
      flushTyping();   // 先把攒着还没发的文字发完，再发按键（顺序反了就成了先回车后打字）
      sendKeyToActive(command);
      return;
    }
    // 落字区聚焦着的时候，可打印字符由它的 input 事件负责（中文/英文都走那条路），
    // 这里再发一次同一批字就成了两遍。
    if (event.target === typingTarget) return;
    var text = typeText(event);
    if (!text) return;
    event.preventDefault();
    event.stopPropagation();
    typingBuffer += text;
    scheduleTypingFlush();
  }, true);
  // 中文等输入法：组合结束拿到的是整段文字，直接并进同一批。
  document.addEventListener('compositionend', function (event) {
    if (!state.active) return;
    var text = event && event.data;
    if (typeof text !== 'string' || !text) return;
    typingBuffer += text;
    scheduleTypingFlush();
  }, true);
  function frameUrl(item, quality) {
    var endpoint = item.endpoint || {};
    var width = quality === 2 ? 1280 : 360;
    var height = quality === 2 ? 900 : 640;
    return 'play.html?shost=' + encodeURIComponent(endpoint.host) + '&sport=' + endpoint.tcpPort + '&q=' + quality + '&v=h264&rtc_i=' + encodeURIComponent(endpoint.host) + '&rtc_p=' + endpoint.udpPort + '&width=' + width + '&height=' + height;
  }
  // 卡片一旦有画面（实时或兜底都要算）就不再显示“画面准备中”，避免看起来是一块白板。
  function markCardReady(item) {
    if (item && item.card) item.card.classList.add('has-picture');
  }
  function setState(id, ready, error) { var card = document.querySelector('.card[data-id="' + CSS.escape(id) + '"]'), node = card && card.querySelector('.state'); if (node) node.className = 'state ' + (error ? 'error' : ready ? 'ready' : ''); if (card) card.classList.toggle('error', !!error); }
  // 连不上就一直重连，但间隔逐次拉长并封顶 10 秒：开机的云机总能恢复，云机也不会被高频打扰。
  // 已经关机的云机由宿主从工作台里移除，页面不需要自己判断开关机。
  var RETRY_MAX_MS = 10000;
  function retry(item) {
    if (!item || !item.live) return;
    // A host can report "powered on" before the cloud machine's WebRTC/RPA
    // endpoints are ready, so the attempts are spaced out up to RETRY_MAX_MS.
    item.retry = (item.retry || 0) + 1;
    var delay = Math.min(RETRY_MAX_MS, 1000 * Math.pow(2, item.retry - 1));
    if (item.retryTimer) window.clearTimeout(item.retryTimer);
    item.retryTimer = window.setTimeout(function () {
      // 暂停期间不推动重连：既省盒子也避免切回来的瞬间一堆连接同时建立。
      if (paused) { item.retryTimer = 0; return; }
      // 这次重连已经用掉了：句柄要清掉，“还在等重连”的判断才不会一直为真（一直为真就再也不会重连）。
      item.retryTimer = 0;
      if (!item.live) return;
      item.live.src = frameUrl(item, 1) + '&retry=' + Date.now();
    }, delay);
  }
  // 用户点了一下正在重试的云机：立刻再连一次，不用等下一轮。
  function restartLive(item) {
    if (!item) return;
    item.retry = 0;
    if (item.retryTimer) { window.clearTimeout(item.retryTimer); item.retryTimer = 0; }
    if (item.live) {
      setState(item.id, false, false);
      item.live.src = frameUrl(item, 1) + '&retry=' + Date.now();
    }
    else acquireLive(item);
  }
  function waitFirstFrame(frame, callback) {
    var started = Date.now();
    function check() {
      try {
        var doc = frame.contentDocument, video = doc && doc.querySelector('video');
        if (video && (video.readyState >= 3 || video.currentTime > 0)) { callback(); return; }
      } catch (_) {}
      if (Date.now() - started < 5000) window.setTimeout(check, 30);
    }
    check();
  }
  function wantedAudio(item) {
    return !!(item && item.id === state.active);
  }
  function hookAudioElement(item, audio) {
    if (!item || !audio || audio.__dashboardAudioHook) return;
    audio.__dashboardAudioHook = true;
    audio.addEventListener('play', function () {
      if (item.id === state.active) return;
      audio.muted = true;
      audio.volume = 0;
      audio.pause();
    });
  }
  function setAudio(item, active) {
    try {
      var doc = item.live && item.live.contentDocument;
      var videos = doc ? doc.querySelectorAll('video') : [], audios = doc ? doc.querySelectorAll('audio') : [];
      var i, j, play;
      for (i = 0; i < videos.length; i++) {
        videos[i].muted = true;
        videos[i].volume = 0;
      }
      for (j = 0; j < audios.length; j++) {
        hookAudioElement(item, audios[j]);
        audios[j].muted = !active;
        if (active) {
          audios[j].volume = 1;
          play = audios[j].play();
          if (play && play.catch) play.catch(function () {});
        } else {
          audios[j].volume = 0;
          audios[j].pause();
        }
      }
    } catch (_) {}
  }
  function enforceAudio(item) {
    if (item && item.live) setAudio(item, wantedAudio(item));
  }
  // 播放器是画面出来之后才建 audio、并在推流到达时自己 play()。
  // 只在 iframe load / 点选时静音会漏：刷新、重连、没有主投屏时，看得见的几路会一起出声。
  function armAudioWatch(item) {
    var tries = 0;
    if (!item) return;
    if (item.audioPoll) window.clearInterval(item.audioPoll);
    enforceAudio(item);
    item.audioPoll = window.setInterval(function () {
      tries++;
      enforceAudio(item);
      if (tries >= 40 || !item.live) {
        window.clearInterval(item.audioPoll);
        item.audioPoll = 0;
      }
    }, 100);
  }
  // 一台云机在预览里只有一个画面元素：当前主画面是实时 iframe，其余是定时静帧 img。
  function surfaceElement(item) { return (item && (item.live || item.still)) || null; }
  function cardScreen(item) {
    return (item && item.card && item.card.querySelector('.screen')) || (item && item.card) || null;
  }
  function layoutSurface(item) {
    if (!item) return;
    var screen = cardScreen(item);
    if (!screen) return;
    var root = document.getElementById('root').getBoundingClientRect();
    var target = item.id === state.active ? state.main.getBoundingClientRect() : screen.getBoundingClientRect();
    // The navigation rail overlays the bottom of the main surface. Exclude it
    // from both video layout and pointer mapping so screen coordinates stay aligned.
    if (item.id === state.active) {
      target = { left: target.left, top: target.top, width: target.width, height: Math.max(1, target.height - MAIN_RAIL_HEIGHT) };
    }
    var scale = Math.min(target.width / 360, target.height / 640);
    var width = 360 * scale, height = 640 * scale;
    var left = (target.left - root.left + (target.width - width) / 2) + 'px';
    var top = (target.top - root.top + (target.height - height) / 2) + 'px';
    var transform = 'scale(' + scale + ')';
    function place(node, zIndex) {
      if (!node) return;
      node.style.left = left;
      node.style.top = top;
      node.style.transform = transform;
      node.style.zIndex = String(zIndex);
    }
    // 实时画面和兜底静帧是两个元素，都要摆到位：实时画面还没出图时静帧要压在它上面，
    // 否则卡片上只有一层空的实时画面，看起来就是空白。
    var liveZIndex = item.id === state.active ? 7 : 1;
    place(item.live, liveZIndex);
    place(item.still, item.live && !item.liveReady ? liveZIndex + 1 : liveZIndex);
  }
  function layoutAll() { state.items.forEach(layoutSurface); }
  var liveSyncPending = false;
  function scheduleLiveSync() {
    if (liveSyncPending) return;
    liveSyncPending = true;
    window.requestAnimationFrame(function () { liveSyncPending = false; syncLiveSet(); });
  }
  // The iframe can contain letterboxing while the player negotiates the
  // device's real aspect ratio. Pointer coordinates must follow the actual
  // video/canvas rectangle, not the fixed 360x640 iframe viewport.
  function getFrameViewportRect(item) {
    var frame = item && item.live;
    if (!frame) return null;
    var outer = frame.getBoundingClientRect();
    if (!outer.width || !outer.height) return null;
    try {
      var doc = frame.contentDocument;
      var candidates = doc ? Array.prototype.slice.call(doc.querySelectorAll('video,canvas')) : [];
      var target = candidates.filter(function (node) {
        var rect = node.getBoundingClientRect();
        return rect.width >= 2 && rect.height >= 2;
      }).sort(function (a, b) {
        var ar = a.getBoundingClientRect(), br = b.getBoundingClientRect();
        return br.width * br.height - ar.width * ar.height;
      })[0];
      if (target && frame.offsetWidth > 0 && frame.offsetHeight > 0) {
        var inner = target.getBoundingClientRect();
        var scaleX = outer.width / frame.offsetWidth;
        var scaleY = outer.height / frame.offsetHeight;
        return {
          left: outer.left + inner.left * scaleX,
          top: outer.top + inner.top * scaleY,
          width: inner.width * scaleX,
          height: inner.height * scaleY
        };
      }
    } catch (_) {}
    return outer;
  }
  function rootWidth() { return root.getBoundingClientRect().width; }
  // 宽度与拖动条的步长对齐：否则最右端停不到上限，拖动条位置也会和实际宽度差一截。
  function alignMainWidth(value) {
    return MIN_MAIN_WIDTH + Math.round((value - MIN_MAIN_WIDTH) / MAIN_WIDTH_STEP) * MAIN_WIDTH_STEP;
  }
  // 画面按手机比例等比放大，工作台高度决定它能长到多大；列再宽也只是多出空白。
  // 上限取“放得下最大画面”的宽度，拖动条整段行程都看得到画面变化。
  function previewWidthLimit() {
    // 用窗口的可用高度，而不是主画面外框自己的高度：外框高度是由宽度算出来的，
    // 再用它去限制宽度就成了闭环，会把主画面越挤越窄。
    var usableHeight = Math.max(1, root.getBoundingClientRect().height - 20) - MAIN_RAIL_HEIGHT;
    if (!(usableHeight > 0)) return Infinity;
    return usableHeight * 360 / 640;
  }
  function maxMainWidth() {
    // 46 是页面左右内边距、两处列间距和分隔条，不参与列宽。
    var room = Math.min(rootWidth() - MIN_SIDE_WIDTH - 46, previewWidthLimit());
    return Math.max(MIN_MAIN_WIDTH,
      MIN_MAIN_WIDTH + Math.floor((room - MIN_MAIN_WIDTH) / MAIN_WIDTH_STEP) * MAIN_WIDTH_STEP);
  }
  function clampMainWidth(value) {
    var width = Math.round(Number(value));
    if (!isFinite(width) || width <= 0) width = DEFAULT_MAIN_WIDTH;
    return Math.max(MIN_MAIN_WIDTH, Math.min(maxMainWidth(), alignMainWidth(width)));
  }
  function storedMainWidth() {
    try {
      var value = Number(window.localStorage.getItem(MAIN_WIDTH_KEY));
      if (isFinite(value) && value > 0) return value;
    } catch (_) {}
    return DEFAULT_MAIN_WIDTH;
  }
  // 记的是用户想要的宽度：窗口变窄时只临时收窄，窗口恢复后设置不会被改小。
  var wantedMainWidth = storedMainWidth(), layoutPending = false;
  function scheduleLayout() {
    if (layoutPending) return;
    layoutPending = true;
    window.requestAnimationFrame(function () { layoutPending = false; layoutAll(); });
  }
  function applyMainWidth(value, remember) {
    wantedMainWidth = Number(value);
    var width = clampMainWidth(wantedMainWidth);
    root.style.setProperty('--left-width', width + 'px');
    applyMainFrameHeight();
    if (remember) { try { window.localStorage.setItem(MAIN_WIDTH_KEY, String(width)); } catch (_) {} }
    scheduleLayout();
    return width;
  }
  // 主画面外框按画面比例收高：上下不留两条空白，框看起来才是跟着画面走的，
  // 底部额外留出快捷操作栏那一条（画面本身仍然按 9:16 铺满可用宽度）。
  function applyMainFrameHeight() {
    var main = state.main;
    if (!main) return;
    var available = Math.max(1, root.getBoundingClientRect().height - 20);
    var width = main.getBoundingClientRect().width;
    if (!(width > 0)) return;
    var height = Math.min(available, Math.round(width * 640 / 360) + MAIN_RAIL_HEIGHT);
    main.style.height = Math.max(1, Math.round(height)) + 'px';
  }
  window.addEventListener('resize', function () { applyMainWidth(wantedMainWidth, false); scheduleLiveSync(); });
  applyMainWidth(wantedMainWidth, false);
  function storedMiniSize() {
    try {
      var value = Number(window.localStorage.getItem(MINI_SIZE_KEY));
      if (isFinite(value) && value > 0) return value;
    } catch (_) {}
    return DEFAULT_MINI_SIZE;
  }
  function alignMiniSize(value) {
    return MINI_SIZE_MIN + Math.round((value - MINI_SIZE_MIN) / MINI_SIZE_STEP) * MINI_SIZE_STEP;
  }
  // 卡片轨道沿用原来的 minmax(140px,180px) 比例：列数按最大轨道尺寸算，
  // 放大后每行自动少放几台，缩小后多放几台。
  function applyMiniSize(value, remember) {
    var size = Math.round(Number(value));
    if (!isFinite(size) || size <= 0) size = DEFAULT_MINI_SIZE;
    size = Math.max(MINI_SIZE_MIN, Math.min(MINI_SIZE_MAX, alignMiniSize(size)));
    if (state.cards) {
      state.cards.style.setProperty('--card-min', size + 'px');
      state.cards.style.setProperty('--card-max', Math.round(size * 180 / 140) + 'px');
    }
    currentMiniSize = size;
    if (remember) { try { window.localStorage.setItem(MINI_SIZE_KEY, String(size)); } catch (_) {} }
    scheduleLayout();
    scheduleLiveSync();
    return size;
  }
  // 主界面那排工具栏拖动画面大小时走这唯一入口，页面自己不再提供第二个拖动条。
  window.__dashboardMiniSize = function (value) { return applyMiniSize(value, true); };
  currentMiniSize = applyMiniSize(storedMiniSize(), false);
  splitter.addEventListener('pointerdown', function (event) { dragging = true; splitter.setPointerCapture(event.pointerId); event.preventDefault(); });
  splitter.addEventListener('pointermove', function (event) {
    if (!dragging) return;
    applyMainWidth(event.clientX - root.getBoundingClientRect().left - 8, true);
  });
  function stopDrag(event) { if (!dragging) return; dragging = false; try { splitter.releasePointerCapture(event.pointerId); } catch (_) {} applyMainFrameHeight(); layoutAll(); }
  splitter.addEventListener('pointerup', stopDrag); splitter.addEventListener('pointercancel', stopDrag);
  // 实时画面“连着”不等于“在出画面”：盒子那路流断掉以后 <video> 元素还在、readyState 也照样是 3/4，
  // 只是画面不再往前走（看着就是黑屏）。所以唯一的判据是播放进度有没有前进，
  // 并且按“这个 video 元素”记进度：重连换了新画面就从零重新记。
  var FROZEN_VIDEO_POLLS = 3;
  function liveProgress(item) {
    var video = null, current = null;
    try {
      var doc = item && item.live && item.live.contentDocument;
      video = doc && doc.querySelector('video');
      if (video && video.readyState >= 2) current = Number(video.currentTime) || 0;
    } catch (_) { video = null; current = null; }
    // 还没有画面数据：不判卡住也不判正常，交给下面的首帧/兜底那套逻辑。
    if (current === null) { item.liveVideo = null; item.liveProgress = null; item.liveFrozen = 0; return 'unknown'; }
    if (item.liveVideo !== video) {
      // 换了新的画面元素（重连、重建）：给新流一个机会，从头开始记。
      item.liveVideo = video; item.liveFrozen = 0;
    } else if (item.liveProgress != null && current <= item.liveProgress + 0.001) {
      item.liveFrozen = (item.liveFrozen || 0) + 1;
    } else {
      item.liveFrozen = 0;
    }
    item.liveProgress = current;
    return item.liveFrozen >= FROZEN_VIDEO_POLLS ? 'stalled' : 'advancing';
  }
  // 画面停住：复位“已出图”这个判断（静帧兜底会立刻恢复，卡片不会再黑着），再按退避重连一次。
  // 注意不要清掉“卡住”的证据：清了的话下一次轮询又会被当成正常，黑屏就会一直挂着绿点。
  function dropStalledLive(item) {
    if (!item || !item.live) return;
    item.liveReady = false;
    if (item.still) item.still.style.display = '';
    setState(item.id, false, true);
    layoutSurface(item);
    // 已经在等重连就别再排一次：每次轮询都重排会把退避计时一直推后，永远等不到重连那一下。
    if (!item.retryTimer) retry(item);
  }
  function watchPlayer(item) {
    item.watch = window.setInterval(function () {
      if (paused) return;
      try {
        enforceAudio(item);
        // 先判“画面是不是卡住了”：卡住却亮着绿点、还不自愈，正是用户踩过的坑，
        // 所以这一条必须排在“已出图”前面，而且不能再用 readyState/currentTime 去把它判成正常。
        var progress = item.live ? liveProgress(item) : 'unknown';
        if (progress === 'stalled') { dropStalledLive(item); return; }
        if (progress === 'advancing') {
          if (!item.liveReady) {
            // 首帧检测没抓到，但画面确实在往前走：交给实时画面，别再往上压兜底静帧。
            item.liveReady = true;
            if (item.still) item.still.style.display = 'none';
            markCardReady(item);
            layoutSurface(item);
          }
          setState(item.id, true, false);
          return;
        }
        // 还没有画面数据：保持当前状态，按播放器自己的提示决定要不要重连。
        var text = (item.live && item.live.contentDocument && item.live.contentDocument.body && item.live.contentDocument.body.innerText) || '';
        if (text.indexOf('连接关闭') >= 0 || text.indexOf('连接失败') >= 0 || text.indexOf('系统繁忙') >= 0) {
          setState(item.id, false, true);
          retry(item);
        }
      } catch (_) {}
    }, 1000);
  }
  function createCard(item) {
    var card = document.createElement('div'); card.className = 'card'; card.setAttribute('data-id', item.id);
    card.setAttribute('data-name', item.name || item.id);
    // 画面下方只写云机名（位置小）；主机、坑位放在悬停提示里，需要时一停就能看到。
    card.title = item.detail || item.name || item.id;
    // 云机名前先给一个坑位号：主机返回的云机顺序不固定，靠这个号对上是哪一台。
    var slot = String(item.slot == null ? '' : item.slot).trim();
    if (slot) card.setAttribute('data-slot', slot);
    var slotTag = slot ? '<span class="slot" title="坑位 ' + esc(slot) + '">' + esc(slot) + '</span>' : '';
    card.innerHTML = '<div class="screen"><span class="state"></span><span class="waiting">画面准备中</span></div><label class="meta"><input type="checkbox" class="cardPick"' + (item.selected ? ' checked' : '') + '>' + slotTag + '<span class="label">' + esc(item.name) + '</span></label>';
    var meta = card.querySelector('.meta');
    if (meta) meta.addEventListener('click', function (event) { event.stopPropagation(); });
    var pick = card.querySelector('.cardPick');
    if (pick) pick.addEventListener('change', function () {
      post({ type: 'pick', id: item.id, selected: !!pick.checked });
    });
    card.addEventListener('click', function () {
      // 再点一次当前正在投屏的那台就是取消主投屏，点别的照常切换。
      if (item.id === state.active) deselect();
      else select(item.id);
    });
    // 右键沿用会话里同一份设备菜单，由宿主按云机编号打开。
    card.addEventListener('contextmenu', function (event) {
      event.preventDefault();
      event.stopPropagation();
      post({ type: 'menu', id: item.id });
    });
    // 悬停就预连接：点缩略图时实时画面已经连好，切换不再等重连。
    card.addEventListener('pointerenter', function () { schedulePrewarm(item); });
    card.addEventListener('pointerleave', function () { cancelPrewarm(item); });
    state.cards.appendChild(card);
    item.card = card;
    // 副屏默认只显示定时静帧：几十台一起预览时不再为每台各开一路实时画面。
    item.still = document.createElement('img');
    item.still.className = 'still';
    item.still.alt = '';
    // 兜底静帧解码失败（回包被截断之类）时浏览器会在图片框左上角画一个破图占位。
    // 这里把图源清掉，卡片上就不会留一个破碎图片，同时按“这一轮没取到图”记账等下一张。
    item.still.addEventListener('error', function () {
      item.still.removeAttribute('src');
      item.frameFailures = (item.frameFailures || 0) + 1;
      if (item.card) item.card.classList.remove('has-picture');
    });
    document.getElementById('surfaces').appendChild(item.still);
    item.lastFrameAt = 0;
  }
  // 连接策略：看得见的云机保持实时画面（点哪台都秒切），看不见的释放、滚进来再连。
  // 上限是硬约束：一路实时画面等于盒子持续编码 + 本机持续解码，几十路会同时拖垮两边；
  // 超出上限或暂时连不上的可见云机由静帧兜底。
  var MAX_LIVE_STREAMS = 12;

  function cardInSideViewport(item) {
    if (!item || !item.card) return false;
    var rect = item.card.getBoundingClientRect();
    if (!rect.width || !rect.height) return false;
    var side = document.getElementById('side');
    var view = side ? side.getBoundingClientRect() : null;
    if (!view || !view.height) return true;
    return rect.bottom > view.top && rect.top < view.bottom;
  }
  function syncLiveSet() {
    if (paused || released) return;   // 收起来了 / 离开预览了：一路都不建（见 setPaused 与 release）
    var wanted = [];
    var active = activeItem();
    if (active) wanted.push(active);
    if (prewarmed && wanted.indexOf(prewarmed) < 0) wanted.push(prewarmed);
    for (var index = 0; index < state.items.length; index++) {
      var item = state.items[index];
      if (!item || wanted.indexOf(item) >= 0) continue;
      if (cardInSideViewport(item)) wanted.push(item);
    }
    for (var index = 0; index < wanted.length && index < MAX_LIVE_STREAMS; index++) acquireLive(wanted[index]);
    for (var index = 0; index < state.items.length; index++) {
      var item = state.items[index];
      if (!item || !item.live) continue;
      var keep = wanted.indexOf(item);
      if (keep >= 0 && keep < MAX_LIVE_STREAMS) continue;
      releaseLive(item);
    }
  }
  // 悬停预连接：把鼠标停着的那台排到最前，点的时候已经连好。
  var PREWARM_DELAY_MS = 160, PREWARM_IDLE_MS = 8000;
  var prewarmed = null;

  function schedulePrewarm(item) {
    if (!item || item.id === state.active) return;
    cancelPrewarm(item);
    item.prewarmTimer = window.setTimeout(function () {
      item.prewarmTimer = 0;
      prewarmLive(item);
    }, PREWARM_DELAY_MS);
  }
  // 只是取消“还没开始的”预连接；已经连上的那一路由空闲超时自己释放。
  function cancelPrewarm(item) {
    if (item && item.prewarmTimer) { window.clearTimeout(item.prewarmTimer); item.prewarmTimer = 0; }
  }
  function prewarmLive(item) {
    if (!item || item.id === state.active) return;
    prewarmed = item;
    syncLiveSet();
    if (item.idleTimer) window.clearTimeout(item.idleTimer);
    item.idleTimer = window.setTimeout(function () {
      item.idleTimer = 0;
      if (prewarmed === item && item.id !== state.active) releaseLive(item);
    }, PREWARM_IDLE_MS);
  }
  function acquireLive(item) {
    if (!item || item.live || paused || released) return;   // 收起来/离开预览时不建（见 setPaused 与 release）
    var frame = document.createElement('iframe');
    frame.allow = 'autoplay';
    document.getElementById('surfaces').appendChild(frame);
    frame.addEventListener('load', function () {
      setState(item.id, false, false);
      armAudioWatch(item);
      waitFirstFrame(frame, function () {
        item.retry = 0;
        if (item.retryTimer) { window.clearTimeout(item.retryTimer); item.retryTimer = 0; }
        setState(item.id, true, false);
        enforceAudio(item);
        // 实时画面一到就盖住静帧：预连接的卡片也不用再取静帧了。
        item.liveReady = true;
        // 新的一路画面：liveProgress 会按 video 元素认出这是新画面，进度基线自动重来。
        if (item.still) item.still.style.display = 'none';
        markCardReady(item);
        layoutSurface(item);
        // 主画面出来了，左下角那条“正在准备投屏…”就该收掉。
        if (item.id === state.active) clearStatus();
      });
    });
    frame.addEventListener('error', function () {
      setState(item.id, false, true);
      retry(item);
      if (item.id === state.active) clearStatus();
    });
    item.liveSince = Date.now();
    item.liveVideo = null;
    item.liveProgress = null;
    item.liveFrozen = 0;
    item.live = frame;
    frame.src = frameUrl(item, 1);
    if (item.still) item.still.style.display = '';
    watchPlayer(item);
    layoutSurface(item);
  }
  function releaseLive(item) {
    if (!item) return;
    if (item.prewarmTimer) { window.clearTimeout(item.prewarmTimer); item.prewarmTimer = 0; }
    if (item.idleTimer) { window.clearTimeout(item.idleTimer); item.idleTimer = 0; }
    if (prewarmed === item) prewarmed = null;
    if (!item.live) return;
    setAudio(item, false);
    if (item.watch) { window.clearInterval(item.watch); item.watch = 0; }
    if (item.retryTimer) { window.clearTimeout(item.retryTimer); item.retryTimer = 0; }
    if (item.audioPoll) { window.clearInterval(item.audioPoll); item.audioPoll = 0; }
    var frame = item.live;
    item.live = null;
    item.liveReady = false;
    item.liveVideo = null;
    item.liveProgress = null;
    item.liveFrozen = 0;
    item.liveSince = 0;
    try { frame.src = 'about:blank'; } catch (_) {}
    try { if (frame.parentNode) frame.parentNode.removeChild(frame); } catch (_) {}
    if (item.still) item.still.style.display = '';
    item.lastFrameAt = 0;
    layoutSurface(item);
  }
  // 取消主投屏：主画面回到空状态，快捷操作暂时没有目标（宿主收到空 id 会清掉当前云机）。
  // 被取消那台的实时画面不在这里拆——它要是还在可视区里就继续留着，点回来还是秒切。
  function deselect() {
    if (!state.active) return;
    // 取消主投屏时，那台云机的实时画面可能还留在可视区里（不拆流是为了点回来能秒切），
    // 但声音必须跟着停：否则听起来像还在投屏。
    flushTyping();   // 攒着还没发出去的字：趁目标还在，先发完再清空
    state.active = null;
    state.items.forEach(function (x) {
      if (x.card) x.card.classList.toggle('active', false);
      if (x.live) setAudio(x, false);
    });
    var empty = document.querySelector('#main .empty');
    if (empty) { empty.textContent = '从右侧点一台云机开始投屏'; empty.style.display = ''; }
    state.input.style.display = 'none';
    blurTypingTarget();   // 没有主画面就没有输入目标：把键盘放开，别继续吃按键
    if (mainControls) mainControls.style.display = 'none';
    clearStatus();
    updateMainName();
    // 主画面空着：同步源还在，只是这会儿没有画面可以操作。
    updateSyncBadge();
    syncLiveSet();
    layoutAll();
    post({ type: 'select', id: '' });
  }
  function select(id) {
    var item = state.items.filter(function (x) { return x.id === id; })[0]; if (!item) return;
    flushTyping();   // 换目标之前，先把攒着没发的字按旧目标发完（否则会发到刚切过去的这一台）
    var old = state.items.filter(function (x) { return x.id === state.active; })[0];
    // 只静音旧画面：它可能在可视区里，是否断开由 syncLiveSet 统一裁决（可见的不能断，否则切回去要重连）。
    if (old && old !== item) setAudio(old, false);
    if (item.prewarmTimer) { window.clearTimeout(item.prewarmTimer); item.prewarmTimer = 0; }
    if (item.idleTimer) { window.clearTimeout(item.idleTimer); item.idleTimer = 0; }
    if (prewarmed === item) prewarmed = null;
    state.active = id;
    state.items.forEach(function (x) { if (x.card) x.card.classList.toggle('active', x.id === id); });
    updateMainName();
    document.querySelector('#main .empty').style.display = 'none';
    state.input.style.display = 'block';
    // 键盘要能直接打字（含中文）：把落字区挪到主画面中间并聚焦，输入法候选框就出现在画面处。
    var picture = typeof state.input.getBoundingClientRect === 'function'
      ? state.input.getBoundingClientRect() : null;
    if (picture && picture.width && picture.height)
      focusTypingTarget(picture.left + picture.width / 2, picture.top + picture.height / 2);
    else focusTypingTarget();
    if (mainControls) mainControls.style.display = '';
    if (item.live && item.liveReady) clearStatus();
    else showPendingStatus();
    // 已经连了好几次没连上的那台：点它等于让它立刻再试一次，不用等下一轮低频重试。
    if (item.retry > 0) restartLive(item);
    else acquireLive(item);
    setAudio(item, true);
    syncLiveSet();
    layoutAll();
    // 主画面换了：这一屏会不会广播（是不是同步源）得立刻反映出来。
    updateSyncBadge();
    post({ type: 'select', id: id });
  }
  // 两种"看不见"要分开处理（都由宿主显式下发，不靠页面自己猜）：
  //   visibility=false —— 临时被挡住（抽屉/浮层）：只停后台截图与重连，实时画面留着，让开就秒回；
  //   release          —— 整页离开预览（切回列表）：**占用的通道全放掉**——十几路实时画面就是
  //                       十几条到云机的连接（还带着声音），人都不看了还留着纯属白占。
  // 切回预览时（visibility=true）按可见集合重建，会有一秒左右的重连，这是明确的取舍。
  var released = false;
  function releaseAllLive() {
    state.items.forEach(function (item) { if (item) releaseLive(item); });
    prewarmed = null;
  }
  function setPaused(value) {
    if (paused === value) return;
    paused = value;
    if (paused) return;
    released = false;   // 又看得见了 = 回到预览：允许重新把画面建起来
    scheduleLayout();
    scheduleLiveSync();
    requestPreviewFrames();
    state.items.forEach(function (item) { if (item && item.live && !item.liveReady) retry(item); });
  }
  function receiveConfiguration(event) {
    var data = event && event.data;
    if (typeof data === 'string') { try { data = JSON.parse(data); } catch (_) { return; } }
    data = data || {};
    if (data.type === 'clipboard_text') { completeHostClipboard(data); return; }
    if (data.type === 'visibility') { setPaused(data.visible === false); return; }
    if (data.type === 'release') { released = true; releaseAllLive(); return; }
    if (data.type === 'sync') {
      window.__dashboardSync(data.enabled === true, data.ids, data.sourceId, data.sourceName);
      return;
    }
    if (data.type === 'selection') {
      var mirrored = findItem(String(data.id == null ? '' : data.id));
      if (mirrored) setCardSelected(mirrored, data.selected === true);
      return;
    }
    if (data.type === 'preview_frame') { completePreviewFrame(data); return; }
    if (data.type === 'configure') {
      // 整份重建前先把上一批收尾：否则上一批的定时器会继续跑，还会去改新卡片的显示状态。
      state.items.forEach(teardownItem);
      state.items = (data.items || []).slice(0, 40); state.cards.innerHTML = ''; document.getElementById('surfaces').innerHTML = '';
      frameCursor = 0; framePending = Object.create(null);
      // 满页重建也按坑位排：卡片建立顺序就是 DOM 顺序。
      orderItems();
      state.items.forEach(createCard);
      if (state.items.length) select(preferredInitialId());
      updateMainName();
      // 卡片刚建出来就要带上同步标记：宿主上一次推的目标集合还在这里。
      applySyncMarks();
      // 宿主指定过的那一台优先于默认选中。
      applyPendingFocus();
      syncLiveSet();
      applyMainWidth(wantedMainWidth, false);
      document.getElementById('side').addEventListener('scroll', function () { scheduleLayout(); scheduleLiveSync(); });
      // 画面大小的值存在页面里，工具栏的拖动条要按这个值初始化。
      post({ type: 'mini_size', value: currentMiniSize });
      return;
    }
    // 云机开关机、新建之后宿主只补发变化的那几台：其余云机的实时画面保持不动，
    // 不会因为开了一台新云机就把整个工作台重连一遍。
    if (data.type === 'add') { addItems(data.items); return; }
    if (data.type === 'remove') { removeItems(data.ids); return; }
  }
  // 宿主指定主画面（列表里右键“打开投屏”）：这一台可能还没出现在工作台里
  // （刚开机、或被后面的集合同步补进来），所以先记住编号，等它出现再选中。
  var pendingFocusId = '';
  function applyPendingFocus() {
    if (!pendingFocusId || !findItem(pendingFocusId)) return;
    var target = pendingFocusId;
    pendingFocusId = '';
    select(target);
  }
  window.__dashboardFocus = function (id) {
    var target = String(id == null ? '' : id);
    if (!target) return;
    pendingFocusId = target;
    applyPendingFocus();
  };

  // 主画面默认给一台：优先已经勾选的那台，让“勾选的目标”和“正在看的那台”一开始就一致。
  function preferredInitialId() {
    for (var index = 0; index < state.items.length; index++) {
      var item = state.items[index];
      if (item && item.selected) return item.id;
    }
    return state.items.length ? state.items[0].id : '';
  }
  function addItems(list) {
    var added = [];
    (list || []).forEach(function (raw) {
      if (!raw || !raw.id) return;
      var id = String(raw.id);
      if (findItem(id) || added.some(function (x) { return x.id === id; })) return;
      added.push({
        id: id,
        name: raw.name || id,
        detail: raw.detail || raw.name || id,
        endpoint: raw.endpoint || {},
        slot: raw.slot,
        selected: !!raw.selected
      });
    });
    if (!added.length) return;
    added.forEach(function (item) { state.items.push(item); createCard(item); });
    applyCardOrder();
    applySyncMarks();
    applyPendingFocus();
    syncLiveSet();
    applyMainWidth(wantedMainWidth, false);
  }
  // 坑位号解析成数字用于排序：解析不出来的（例如 V2 云机没有坑位）留在本主机最后。
  function slotOrder(value) {
    var digits = String(value == null ? '' : value).replace(/[^0-9]/g, '');
    if (!digits) return Infinity;
    var parsed = parseInt(digits, 10);
    return isNaN(parsed) ? Infinity : parsed;
  }
  function itemHost(item) {
    if (!item) return '';
    if (item.host) return String(item.host);
    return String((item.endpoint && item.endpoint.host) || '');
  }
  // 排序口径：主机之间保持它们第一次出现的先后（不按地址重排，免得每次同步都换位置），
  // 同一个主机内部按坑位号从小到大；没有坑位号的排在最后，相等的一律保持原有先后
  // （Array.sort 稳定），所以没有坑位信息的场景不会被“排”出新顺序。
  function orderItems() {
    var ranks = Object.create(null);
    var next = 1;
    state.items.forEach(function (item) {
      var host = itemHost(item);
      if (!Object.prototype.hasOwnProperty.call(ranks, host)) ranks[host] = next++;
    });
    state.items.sort(function (left, right) {
      var leftRank = ranks[itemHost(left)], rightRank = ranks[itemHost(right)];
      if (leftRank !== rightRank) return leftRank - rightRank;
      var leftSlot = slotOrder(left && left.slot), rightSlot = slotOrder(right && right.slot);
      if (leftSlot === rightSlot) return 0;
      return leftSlot < rightSlot ? -1 : 1;
    });
  }
  function applyCardOrder() {
    orderItems();
    // appendChild 只是把已有卡片挪到新位置，事件不会丢，也不会重复建卡片。
    state.items.forEach(function (item) { if (item && item.card) state.cards.appendChild(item.card); });
  }
  function removeItems(ids) {
    var gone = Object.create(null), count = 0;
    (ids || []).forEach(function (id) { id = String(id == null ? '' : id); if (id) { gone[id] = true; count++; } });
    if (!count) return;
    var removed = state.items.filter(function (item) { return item && gone[item.id]; });
    if (!removed.length) return;
    // 被移除的正好是当前主画面：先按“取消主投屏”收尾并告诉宿主，再把卡片拆掉。
    if (state.active && gone[state.active]) deselect();
    state.items = state.items.filter(function (item) { return !(item && gone[item.id]); });
    removed.forEach(teardownItem);
    updateMainName();
    syncLiveSet();
    scheduleLayout();
  }
  function teardownItem(item) {
    if (!item) return;
    releaseLive(item);
    if (item.prewarmTimer) { window.clearTimeout(item.prewarmTimer); item.prewarmTimer = 0; }
    if (item.idleTimer) { window.clearTimeout(item.idleTimer); item.idleTimer = 0; }
    if (prewarmed === item) prewarmed = null;
    if (item.card && item.card.parentNode) item.card.parentNode.removeChild(item.card);
    if (item.still && item.still.parentNode) item.still.parentNode.removeChild(item.still);
    item.card = null;
    item.still = null;
  }
  if (window.chrome && window.chrome.webview) window.chrome.webview.addEventListener('message', receiveConfiguration);
  function pointer(type, event) {
    if (!state.active) return;
    var activeItem = state.items.filter(function (x) { return x.id === state.active; })[0];
    var rect = activeItem && activeItem.live ? getFrameViewportRect(activeItem) : state.input.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    var x = (event.clientX - rect.left) / rect.width;
    var y = (event.clientY - rect.top) / rect.height;
    if (x < 0 || x > 1 || y < 0 || y > 1) return;
    post({ type: type, id: state.active, x: x, y: y, pointerId: event.pointerId || 1 });
    event.preventDefault();
  }
  var pointerId = null;
  state.input.addEventListener('pointerdown', function (e) {
    if (e.button !== 0) return;
    // 点画面 = 我要往这台云机里打字：把落字区挪到光标处并重新聚焦（点画面会把焦点从它身上带走）。
    focusTypingTarget(e.clientX, e.clientY);
    pointerId = e.pointerId; state.input.setPointerCapture(e.pointerId); pointer('down', e);
  });
  state.input.addEventListener('pointermove', function (e) { if (pointerId === e.pointerId) pointer('move', e); });
  function finish(e) { if (pointerId !== e.pointerId) return; pointer('up', e); pointerId = null; }
  state.input.addEventListener('pointerup', finish); state.input.addEventListener('pointercancel', finish);
  // 副屏静帧调度：兜底画面每台每秒一张，同时在途请求数有上限。
  // 截图在云机上执行（V3 是一次 ADB 截屏），所以速率必须固定住；
  // 台数多时靠在途上限排队轮转，不会同时把盒子打满。连不上时按次退避，最多 4 秒一次。
  var FRAME_INTERVAL_MS = 1000, FRAME_MAX_INTERVAL_MS = 4000, FRAME_MAX_IN_FLIGHT = 2;
  // 实时画面建起来之后多久还没出图就改用静帧兜底：云机开机中、网络不通、连不上时
  // 那一路是一直空的，不给兜底的话卡片就是一块白板。
  var LIVE_FALLBACK_GRACE_MS = 3000;
  // 在途请求只按 requestId 记账，在途数由这张表派生：某台云机在回包前关机又开机
  // （卡片被移除再加回来）时，名额跟着请求走，不会被同一个 id 的另一轮请求顶掉。
  var frameRequestSeq = 0, frameCursor = 0, framePending = Object.create(null);

  function frameInFlightCount() {
    var count = 0;
    for (var key in framePending) count++;
    return count;
  }
  // 该不该给这台取静帧（不管在途状态和节流间隔）。
  function frameWanted(item) {
    if (!item || !item.card || !item.still) return false;
    // 实时画面已经出图的才不重复取；只是"建起来了"不算——连不上的那一路一直是空的，
    // 这时候必须用静帧兜底（宽限期是为了不打断正在建立的正常连接）。
    if (item.live && (item.liveReady || Date.now() - (item.liveSince || 0) < LIVE_FALLBACK_GRACE_MS)) return false;
    if (item.still.style.display === 'none') return false;
    var rect = item.card.getBoundingClientRect();
    if (!rect.width || !rect.height) return false;
    var side = document.getElementById('side');
    var view = side ? side.getBoundingClientRect() : null;
    if (!view || !view.height) return true;
    return rect.bottom > view.top && rect.top < view.bottom;
  }
  function stillWanted(item) {
    return !!item && !item.framePending && frameWanted(item);
  }
  function requestPreviewFrames() {
    var items = state.items;
    if (!items.length) return;
    // 工作台收起来时不再给云机截图（暂停由宿主显式下发，不依赖页面自己判断可见性）；
    // 实时画面按原有策略保留，所以切回来仍然是秒开。
    if (paused || released || document.hidden) return;
    var now = Date.now();
    var inFlight = frameInFlightCount();
    for (var step = 0; step < items.length && inFlight < FRAME_MAX_IN_FLIGHT; step++) {
      frameCursor = (frameCursor + 1) % items.length;
      var item = items[frameCursor];
      if (!stillWanted(item)) continue;
      // 正常每秒一张；连续取不到时按次退避，最多 4 秒一张。
      var wait = item.frameFailures
        ? Math.min(FRAME_MAX_INTERVAL_MS, FRAME_INTERVAL_MS * Math.pow(2, item.frameFailures))
        : FRAME_INTERVAL_MS;
      if (item.lastFrameAt && now - item.lastFrameAt < wait) continue;
      var requestId = 'frame-' + (++frameRequestSeq);
      item.framePending = true;
      item.frameRequestId = requestId;
      framePending[requestId] = item;
      inFlight++;
      post({ type: 'preview_frame', id: item.id, requestId: requestId });
    }
  }
  function completePreviewFrame(message) {
    var requestId = String((message && message.requestId) || '');
    // 名额只跟 requestId 走：卡片可能在回包前就被移除（关机）或已经发起了新的一轮请求。
    var owner = framePending[requestId];
    if (owner) {
      delete framePending[requestId];
      if (owner.frameRequestId === requestId) { owner.framePending = false; owner.frameRequestId = ''; }
    }
    var id = String((message && message.id) || '');
    var item = findItem(id);
    if (!item) return;
    item.lastFrameAt = Date.now();
    if (!message.ok || !message.data) {
      item.frameFailures = (item.frameFailures || 0) + 1;
      // 已经有实时画面时，静帧失败不能盖上“正在重试”：画面其实是好的。
      if (!item.live && !item.liveReady && item.frameFailures >= 2) setState(id, false, true);
      return;
    }
    item.frameFailures = 0;
    // 只有实时画面真的出图了才丢掉这张兜底：连不上的那一路不能把卡片留在空白状态。
    if (item.liveReady) return;
    if (item.still) item.still.src = 'data:image/' + (message.format || 'jpeg') + ';base64,' + message.data;
    markCardReady(item);
    layoutSurface(item);
    setState(id, true, false);
  }
  window.setInterval(requestPreviewFrames, 120);
  // 切回列表模式时工作台只是被隐藏（页面继续活着），重新显示时要再排一次版并补齐实时画面：
  // 隐藏期间新增的云机拿不到可见区域，不会自己连上。
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) return;
    scheduleLayout();
    scheduleLiveSync();
    requestPreviewFrames();
  });
  window.__dashboardSelect = select;
})();
