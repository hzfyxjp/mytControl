// The bundled player exposes a legacy UTF-8 helper that encodes UTF-16
// surrogate halves separately. That turns emoji into invalid UTF-8 and the
// remote Android input service renders replacement characters (�).
(function () {
    if (typeof LGAIR === "undefined") return;

    // The bundled player assumes both data channels are ready whenever an
    // input callback fires. WebRTC can report the page as loaded earlier, and
    // sending several text frames synchronously can also overtake a command.
    // Keep all text and key commands in one ordered queue and apply bounded
    // WebRTC backpressure before sending the next frame.
    var originalSendInputText = LGAIR.prototype.send_input_txt;
    var originalSendCommand = LGAIR.prototype.sendCmdEvent;
    var sendQueue = [];
    var sendPumpTimer = 0;
    var sendPumpRunning = false;
    var nextClipboardRequestId = 0;
    var pendingClipboardRequests = Object.create(null);
    var pendingClipboardOrder = [];
    var pendingKeyboardPaste = null;
    var sendRetryMs = 20;
    var sendDeadlineMs = 10000;
    var clipboardDeadlineMs = 2000;
    var sendBufferedAmountLimit = 256 * 1024;

    function logDebug(instance, text) {
        if (instance && typeof instance.fun_log === "function")
            instance.fun_log(text, instance.LOG_DEBUG);
    }

    function getOpenDataChannel(instance) {
        var player = instance && instance.g_player;
        var channel = player && player.sendChannel;
        if (!player || typeof player.sendData !== "function" || !channel) return null;
        if (typeof channel.readyState !== "undefined" && channel.readyState !== "open") return null;
        if (typeof channel.bufferedAmount === "number" &&
            channel.bufferedAmount > sendBufferedAmountLimit) return null;
        return channel;
    }

    function sendFrame(instance, frame) {
        var channel = getOpenDataChannel(instance);
        if (!channel) return null;

        try {
            instance.g_player.sendData(frame);
        } catch (error) {
            logDebug(instance, "send input failed: " + error);
            return false;
        }

        // The control socket is an auxiliary path used by the original
        // player. It may still be connecting while WebRTC is already usable;
        // never let that secondary path delay or invalidate the primary send.
        var control = instance.g_mctrl_socket;
        var socket = control && control.ctr_socket;
        if (control && typeof control.sendData === "function" && socket && socket.readyState === 1) {
            try { control.sendData(frame); }
            catch (error) { logDebug(instance, "control input failed: " + error); }
        }
        return true;
    }

    function createTextFrames(instance, value, type) {
        value = value == null ? "" : String(value);
        if (!value) return [];

        var bytes = LGAIR.string2buffer(LGAIR.str2utf8(value));
        var messageType = typeof type === "undefined" ? instance.SDL_EVENT_MSGTYPE_KEYTEXT : type;
        var frames = [];
        for (var offset = 0; offset < bytes.length;) {
            var end = Math.min(offset + 50, bytes.length);
            while (end < bytes.length && (bytes[end] & 0xC0) === 0x80 && end > offset) end--;
            if (end <= offset) end = Math.min(offset + 50, bytes.length);

            var chunkLength = end - offset;
            var continuation = end < bytes.length ? 1 : 0;
            var payloadLength = 6 + chunkLength;
            var frameLength = payloadLength + 8;
            var frame = new ArrayBuffer(frameLength);
            var view = new DataView(frame);
            var cursor = 0;
            view.setUint32(cursor, instance.MSG_TYPE_CTRL); cursor += 4;
            view.setUint32(cursor, frameLength); cursor += 4;
            view.setUint16(cursor, payloadLength); cursor += 2;
            view.setUint8(cursor++, messageType);
            view.setUint8(cursor++, continuation);
            view.setUint16(cursor, chunkLength); cursor += 2;
            new Uint8Array(frame, cursor, chunkLength).set(bytes.subarray(offset, end));
            frames.push(view);
            offset = end;
        }
        return frames;
    }

    function createCommandFrame(instance, command) {
        var codes = {
            goClean: [257, 7],
            goHome: [257, 6],
            goBack: [257, 5],
            volUp: [257, 8],
            volDown: [257, 9],
            VK_BACK: [258, 1],
            VK_DELETE: [258, 1],
            VK_RETURN: [258, 2],
            VK_RIGHT: [258, 3],
            VK_LEFT: [258, 4],
            VK_UP: [258, 5],
            VK_DOWN: [258, 6]
        };
        var code = Object.prototype.hasOwnProperty.call(codes, command) ? codes[command] : [0, 0];
        var frame = new ArrayBuffer(20);
        var view = new DataView(frame);
        view.setUint32(0, instance.MSG_TYPE_CTRL);
        view.setUint32(4, 20);
        view.setUint16(8, 12);
        view.setUint8(10, instance.SDL_EVENT_MSGTYPE_KEYCMD);
        view.setUint8(11, 0);
        view.setUint32(12, code[0]);
        view.setUint32(16, code[1]);
        return view;
    }

    function scheduleSendPump(delay) {
        if (sendPumpTimer) return;
        sendPumpTimer = window.setTimeout(function () {
            sendPumpTimer = 0;
            pumpSendQueue();
        }, Math.max(0, delay || 0));
    }

    function pumpSendQueue() {
        if (sendPumpRunning) return;
        sendPumpRunning = true;
        var steps = 0;
        try {
            while (sendQueue.length > 0 && steps < 64) {
                var item = sendQueue[0];
                if (item.kind === "clipboard") {
                    if (!item.resolved) {
                        if (Date.now() >= item.deadline) {
                            delete pendingClipboardRequests[item.requestId];
                            pendingClipboardOrder.shift();
                            sendQueue.shift();
                            continue;
                        }
                        scheduleSendPump(sendRetryMs);
                        return;
                    }

                    sendQueue.shift();
                    if (item.succeeded && item.text) {
                        var clipboardFrames = createTextFrames(item.instance, item.text, item.type);
                        if (clipboardFrames.length > 0) {
                            sendQueue.unshift({
                                kind: "frames",
                                instance: item.instance,
                                frames: clipboardFrames,
                                index: 0,
                                deadline: Date.now() + sendDeadlineMs
                            });
                        }
                    }
                    continue;
                }

                if (Date.now() >= item.deadline) {
                    logDebug(item.instance, "input queue item expired");
                    sendQueue.shift();
                    continue;
                }

                var frame = item.kind === "frames" ? item.frames[item.index] : item.frame;
                var sent = sendFrame(item.instance, frame);
                if (sent === null) {
                    scheduleSendPump(sendRetryMs);
                    return;
                }
                if (!sent) {
                    sendQueue.shift();
                    continue;
                }

                steps++;
                if (item.kind === "frames") {
                    item.index++;
                    if (item.index >= item.frames.length) sendQueue.shift();
                } else {
                    sendQueue.shift();
                }
            }
        } finally {
            sendPumpRunning = false;
        }

        if (sendQueue.length > 0) scheduleSendPump(0);
    }

    function enqueueText(instance, value, type) {
        var frames = createTextFrames(instance, value, type);
        if (frames.length === 0) return;
        sendQueue.push({
            kind: "frames",
            instance: instance,
            frames: frames,
            index: 0,
            deadline: Date.now() + sendDeadlineMs
        });
        pumpSendQueue();
    }

    function enqueueCommand(instance, command) {
        sendQueue.push({
            kind: "command",
            instance: instance,
            frame: createCommandFrame(instance, command),
            deadline: Date.now() + sendDeadlineMs
        });
        pumpSendQueue();
    }

    if (typeof originalSendInputText === "function") {
        LGAIR.prototype.send_input_txt = function (value, type) {
            enqueueText(this, value, type);
        };
    }
    if (typeof originalSendCommand === "function") {
        LGAIR.prototype.sendCmdEvent = function (command) {
            enqueueCommand(this, command);
        };
    }

    // The bundled player leaves this callback empty, but on_input_end expects
    // a string and reads its length.  Always return and clear the pending text.
    if (typeof LGAIR_UI !== "undefined") {
        LGAIR_UI.hide_input = function () {
            var input = document.getElementById("input_text");
            if (!input) return "";
            var value = input.value || "";
            input.value = "";
            return value;
        };
    }

    LGAIR.str2utf8 = function (value) {
        value = value == null ? "" : String(value);
        var bytes = "";

        function append(codePoint) {
            if (codePoint <= 0x7F) {
                bytes += String.fromCharCode(codePoint);
            } else if (codePoint <= 0x7FF) {
                bytes += String.fromCharCode(0xC0 | (codePoint >> 6));
                bytes += String.fromCharCode(0x80 | (codePoint & 0x3F));
            } else if (codePoint <= 0xFFFF) {
                bytes += String.fromCharCode(0xE0 | (codePoint >> 12));
                bytes += String.fromCharCode(0x80 | ((codePoint >> 6) & 0x3F));
                bytes += String.fromCharCode(0x80 | (codePoint & 0x3F));
            } else {
                bytes += String.fromCharCode(0xF0 | (codePoint >> 18));
                bytes += String.fromCharCode(0x80 | ((codePoint >> 12) & 0x3F));
                bytes += String.fromCharCode(0x80 | ((codePoint >> 6) & 0x3F));
                bytes += String.fromCharCode(0x80 | (codePoint & 0x3F));
            }
        }

        for (var index = 0; index < value.length; index++) {
            var codeUnit = value.charCodeAt(index);
            var codePoint = codeUnit;
            if (codeUnit >= 0xD800 && codeUnit <= 0xDBFF) {
                var next = index + 1 < value.length ? value.charCodeAt(index + 1) : 0;
                if (next >= 0xDC00 && next <= 0xDFFF) {
                    codePoint = 0x10000 + ((codeUnit - 0xD800) << 10) + (next - 0xDC00);
                    index++;
                } else {
                    codePoint = 0xFFFD;
                }
            } else if (codeUnit >= 0xDC00 && codeUnit <= 0xDFFF) {
                codePoint = 0xFFFD;
            }
            append(codePoint);
        }
        return bytes;
    };

    // The legacy helper also drops single-digit hexadecimal bytes such as
    // LF and CR. Convert the binary string directly so pasted line breaks
    // reach the remote input service unchanged.
    LGAIR.string2buffer = function (value) {
        value = value == null ? "" : String(value);
        var result = new Uint8Array(value.length);
        for (var index = 0; index < value.length; index++) {
            result[index] = value.charCodeAt(index) & 0xFF;
        }
        return result;
    };

    function removePendingClipboardRequest(requestId) {
        for (var index = 0; index < pendingClipboardOrder.length; index++) {
            if (pendingClipboardOrder[index] === requestId) {
                pendingClipboardOrder.splice(index, 1);
                return;
            }
        }
    }

    function completeClipboardRequest(requestId, succeeded, value) {
        var item = pendingClipboardRequests[requestId];
        if (!item || item.resolved) return;
        item.resolved = true;
        item.succeeded = !!succeeded;
        item.text = value == null ? "" : String(value);
        delete pendingClipboardRequests[requestId];
        removePendingClipboardRequest(requestId);
        if (pendingKeyboardPaste && pendingKeyboardPaste.item === item)
            pendingKeyboardPaste = null;
        pumpSendQueue();
    }

    function takePendingClipboardRequest() {
        while (pendingClipboardOrder.length > 0) {
            var requestId = pendingClipboardOrder[0];
            var item = pendingClipboardRequests[requestId];
            if (item && !item.resolved) return item;
            pendingClipboardOrder.shift();
        }
        return null;
    }

    function sendPastedText(value) {
        value = value == null ? "" : String(value);
        var pending = takePendingClipboardRequest();
        if (pending) {
            completeClipboardRequest(pending.requestId, true, value);
            return;
        }
        if (!value || !window.h5_lgair || typeof window.h5_lgair.send_input_txt !== "function") return;
        window.h5_lgair.send_input_txt(value);
    }

    function hasHostClipboardBridge() {
        return !!(window.chrome && window.chrome.webview &&
            typeof window.chrome.webview.postMessage === "function");
    }

    function createClipboardRequest() {
        var instance = window.h5_lgair;
        if (!instance || typeof instance.send_input_txt !== "function") return null;
        var requestId = "clipboard-" + (++nextClipboardRequestId);
        var item = {
            kind: "clipboard",
            instance: instance,
            requestId: requestId,
            type: instance.SDL_EVENT_MSGTYPE_KEYTEXT,
            resolved: false,
            succeeded: false,
            text: "",
            deadline: Date.now() + clipboardDeadlineMs
        };
        pendingClipboardRequests[requestId] = item;
        pendingClipboardOrder.push(requestId);
        sendQueue.push(item);
        pumpSendQueue();
        return item;
    }

    function dispatchClipboardRequest(item) {
        if (!item || item.resolved) return;
        if (hasHostClipboardBridge()) {
            try {
                window.chrome.webview.postMessage(JSON.stringify({
                    type: "clipboard_request",
                    requestId: item.requestId
                }));
            } catch (error) {
                completeClipboardRequest(item.requestId, false, "");
            }
            return;
        }

        if (!navigator.clipboard || typeof navigator.clipboard.readText !== "function") {
            completeClipboardRequest(item.requestId, false, "");
            return;
        }
        try {
            navigator.clipboard.readText().then(function (value) {
                completeClipboardRequest(item.requestId, true, value);
            }).catch(function (error) {
                completeClipboardRequest(item.requestId, false, "");
            });
        } catch (error) {
            completeClipboardRequest(item.requestId, false, "");
        }
    }

    function readClipboardText() {
        dispatchClipboardRequest(createClipboardRequest());
    }

    function handleClipboardMessage(event) {
        var message = event && event.data;
        if (typeof message === "string") {
            try { message = JSON.parse(message); }
            catch (error) { return; }
        }
        if (!message || message.type !== "clipboard_text") return;
        completeClipboardRequest(String(message.requestId || ""), !!message.succeeded, message.text || "");
    }

    if (window.chrome && window.chrome.webview &&
        typeof window.chrome.webview.addEventListener === "function") {
        window.chrome.webview.addEventListener("message", handleClipboardMessage);
    }

    document.addEventListener("paste", function (event) {
        if (!event.target || event.target.id !== "input_text") return;
        event.preventDefault();
        event.stopPropagation();
        if (pendingKeyboardPaste) {
            pendingKeyboardPaste.cancelled = true;
            pendingKeyboardPaste = null;
        }
        var clipboardData = event.clipboardData;
        if (clipboardData && typeof clipboardData.getData === "function") {
            sendPastedText(clipboardData.getData("text/plain"));
        } else {
            readClipboardText();
        }
    }, true);

    document.addEventListener("keydown", function (event) {
        if (!event.target || event.target.id !== "input_text" || !event.ctrlKey) return;
        var key = String(event.key || "").toLowerCase();
        if (key !== "v" && event.keyCode !== 86 && event.which !== 86) return;
        if (!hasHostClipboardBridge() &&
            (!navigator.clipboard || typeof navigator.clipboard.readText !== "function")) return;
        event.preventDefault();
        event.stopPropagation();
        if (pendingKeyboardPaste) pendingKeyboardPaste.cancelled = true;
        // Reserve the queue position synchronously. The clipboard read itself
        // is still dispatched on the next turn so a browser paste event, when
        // available, can satisfy this request without creating a duplicate.
        var request = { cancelled: false, item: createClipboardRequest() };
        pendingKeyboardPaste = request;
        window.setTimeout(function () {
            if (request.cancelled || !request.item || request.item.resolved) return;
            if (pendingKeyboardPaste === request) pendingKeyboardPaste = null;
            dispatchClipboardRequest(request.item);
        }, 0);
    }, true);
})();
