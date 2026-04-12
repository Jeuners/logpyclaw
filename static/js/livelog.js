/**
 * Livelog Panel — SSE-basiertes Event-Log im Chat.
 * Toggle via Topbar-Button, streamt /api/logs/stream.
 */
(function() {
    'use strict';

    const MAX_ENTRIES = 200;
    let evtSource = null;
    let isOpen = false;

    const ICON_MAP = {
        'chat_message':    { icon: 'chat',          color: '#00e676' },
        'task_result':     { icon: 'task_alt',       color: '#29b6f6' },
        'task':            { icon: 'task_alt',       color: '#29b6f6' },
        'a2a_dispatch':    { icon: 'swap_horiz',     color: '#ffa726' },
        'heartbeat':       { icon: 'favorite',       color: '#ef5350' },
        'heartbeat_result':{ icon: 'favorite_border',color: '#ef5350' },
        'activity_start':  { icon: 'play_arrow',     color: '#66bb6a' },
        'activity_step':   { icon: 'trending_flat',  color: '#42a5f5' },
        'activity_end':    { icon: 'stop',           color: '#78909c' },
        'skill_exec':      { icon: 'build',          color: '#ab47bc' },
        'error':           { icon: 'error',          color: '#ef4444' },
        'system':          { icon: 'memory',         color: '#78909c' },
    };

    function getPanel() { return document.getElementById('ac-livelog'); }
    function getBody()  { return document.getElementById('ac-livelog-body'); }
    function getCount() { return document.getElementById('ac-livelog-count'); }

    function formatTime(ts) {
        if (!ts) return '';
        try {
            const d = new Date(ts);
            return d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        } catch { return ''; }
    }

    function formatEvent(ev) {
        const type = ev.type || 'unknown';
        const mapped = ICON_MAP[type] || { icon: 'info', color: '#3a5a3a' };
        const time = formatTime(ev.ts);
        const data = ev.data || {};

        // Hilfsfunktion: Text kürzen
        const cut = (s, n) => s ? String(s).substring(0, n || 80) : '';
        const agentLabel = (d) => d.agent_name || (d.agent_id ? d.agent_id.substring(0, 8) + '…' : '');

        let detail = '';
        if (type === 'chat_message') {
            const role = data.role || '?';
            const agent = agentLabel(data);
            const sys = role === 'system' || role === 'assistant';
            const roleColor = sys ? '#78909c' : '#00e676';
            detail = `<span style="color:${roleColor}">${role}</span>`
                   + (agent ? ` <span style="color:#3a5a3a">${agent}</span>` : '')
                   + `: ${cut(data.content, 90)}`;
        } else if (type === 'task_result' || type === 'task') {
            const id = cut(data.task_id || data.id || '', 8);
            const status = data.status || '?';
            const label = cut(data.label || data.message || '', 60);
            const scolor = status === 'completed' ? '#00e676' : status === 'failed' ? '#ef4444' : '#ffa726';
            detail = (id ? `<span style="color:#3a5a3a">${id}…</span> ` : '')
                   + (label ? `${label} ` : '')
                   + `→ <span style="color:${scolor}">${status}</span>`;
        } else if (type === 'a2a_dispatch') {
            const from = data.sender_agent_name || data.sender || '?';
            const to = data.recipient_agent_name || data.recipient || '?';
            detail = `<span style="color:#ffa726">${from}</span> → <span style="color:#ffa726">${to}</span>: ${cut(data.message || data.task_text, 60)}`;
        } else if (type === 'activity_start' || type === 'activity_step' || type === 'activity_end') {
            detail = agentLabel(data) + (data.description ? ': ' + cut(data.description, 70) : '');
        } else if (type === 'heartbeat' || type === 'heartbeat_result') {
            detail = agentLabel(data) || 'ping';
        } else if (type === 'system') {
            detail = agentLabel(data) + (data.content ? ': ' + cut(data.content, 80) : '');
        } else {
            // Fallback: wichtigste Keys extrahieren statt rohen JSON-Dump
            const keys = ['label', 'message', 'content', 'description', 'status', 'name'];
            const found = keys.map(k => data[k]).filter(Boolean);
            detail = found.length ? cut(found[0], 90) : `[${type}]`;
        }

        return `<div style="display:flex;align-items:flex-start;gap:6px;padding:3px 0;font-size:11px;line-height:1.4;border-bottom:1px solid #0a150b">
            <span style="color:${mapped.color};flex-shrink:0;font-size:13px;line-height:1" class="material-icons">${mapped.icon}</span>
            <span style="color:#3a5a3a;flex-shrink:0;font-family:monospace;font-size:10px;min-width:56px">${time}</span>
            <span style="color:#7a9a7a;word-break:break-all">${detail}</span>
        </div>`;
    }

    function checkServices() {
        const body = getBody();
        if (!body) return;
        fetch('/api/providers/status')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                const parts = Object.keys(data).map(function(pid) {
                    const info = data[pid];
                    const color = info.ok ? '#00e676' : '#ef4444';
                    const icon = info.ok ? 'check_circle' : 'cancel';
                    const label = pid.charAt(0).toUpperCase() + pid.slice(1);
                    return '<span style="display:inline-flex;align-items:center;gap:3px;margin-right:10px">' +
                        '<span class="material-icons" style="font-size:12px;color:' + color + '">' + icon + '</span>' +
                        '<span style="color:' + color + '">' + label + '</span>' +
                        '<span style="color:#3a5a3a">' + (info.info || '') + '</span></span>';
                });
                const html = '<div style="padding:4px 0;font-size:10px;border-bottom:1px solid #182e18;margin-bottom:4px;display:flex;flex-wrap:wrap;gap:2px">' +
                    parts.join('') + '</div>';
                body.insertAdjacentHTML('afterbegin', html);
            })
            .catch(function() {});
    }

    function toggle() {
        const panel = getPanel();
        if (!panel) return;
        isOpen = !isOpen;
        panel.style.display = isOpen ? 'flex' : 'none';

        const btn = document.getElementById('ac-livelog-btn');
        if (btn) btn.style.color = isOpen ? '#00e676' : '#3a5a3a';

        if (isOpen && !evtSource) {
            checkServices();
            startStream();
        } else if (!isOpen && evtSource) {
            evtSource.close();
            evtSource = null;
        }
    }

    function startStream() {
        if (evtSource) evtSource.close();
        evtSource = new EventSource('/api/logs/stream?since=0');

        evtSource.onmessage = function(e) {
            try {
                const ev = JSON.parse(e.data);
                const body = getBody();
                if (!body) return;
                body.insertAdjacentHTML('beforeend', formatEvent(ev));
                // Limit entries
                while (body.children.length > MAX_ENTRIES) {
                    body.removeChild(body.firstChild);
                }
                // Auto-scroll nur wenn schon am Ende
                const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 40;
                if (atBottom) body.scrollTop = body.scrollHeight;
                // Update count
                const cnt = getCount();
                if (cnt) cnt.textContent = body.children.length;
            } catch {}
        };

        evtSource.onerror = function() {
            // Reconnect after 3s
            evtSource.close();
            evtSource = null;
            if (isOpen) {
                setTimeout(startStream, 3000);
            }
        };
    }

    function clearLog() {
        const body = getBody();
        if (body) body.innerHTML = '';
        const cnt = getCount();
        if (cnt) cnt.textContent = '0';
    }

    // Init
    document.addEventListener('DOMContentLoaded', function() {
        const btn = document.getElementById('ac-livelog-btn');
        if (btn) btn.addEventListener('click', toggle);

        const clearBtn = document.getElementById('ac-livelog-clear');
        if (clearBtn) clearBtn.addEventListener('click', clearLog);
    });

    // Cleanup on page unload
    window.addEventListener('beforeunload', function() {
        if (evtSource) evtSource.close();
    });
})();
