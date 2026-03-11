/**
 * Application Gateway Auth — Frontend SDK
 *
 * Provides:
 *   - ApplicationGatewayAuth.fetch()  — fetch wrapper with transparent 401 retry
 *   - Proactive token refresh before expiration
 *
 * Usage:
 *   ApplicationGatewayAuth.init({
 *       refreshEndpoint: "/api/auth/refresh",
 *       tokenInfoEndpoint: "/api/auth/token-info",
 *       refreshBeforeExpiry: 300,  // seconds before expiry to refresh
 *       onSessionExpired: () => window.location.reload(),
 *   });
 *
 *   const resp = await ApplicationGatewayAuth.fetch("/api/data", { method: "POST" });
 */
const ApplicationGatewayAuth = (() => {
    let _config = {
        refreshEndpoint: "/api/auth/refresh",
        tokenInfoEndpoint: "/api/auth/token-info",
        refreshBeforeExpiry: 300,
        onSessionExpired: () => {
            window.location.reload();
        },
        maxRetries: 1,
    };

    let _refreshTimer = null;
    let _refreshPromise = null;
    let _initialized = false;

    // ── Public API ─────────────────────────────────────────────

    function init(options = {}) {
        Object.assign(_config, options);
        _initialized = true;
        _scheduleRefreshFromServer();
    }

    async function authFetch(url, options = {}) {
        const response = await fetch(url, options);

        if (response.status === 401) {
            // Skip auth retry for explicitly excluded endpoints
            if (typeof url === "string" && _config.skipRetryPaths) {
                for (const path of _config.skipRetryPaths) {
                    if (url.startsWith(path)) {
                        return response;
                    }
                }
            }

            const refreshed = await _doRefresh();
            if (refreshed) {
                return fetch(url, options);
            }

            _config.onSessionExpired();
        }

        return response;
    }

    async function refresh() {
        return _doRefresh();
    }

    // ── Internal ───────────────────────────────────────────────

    async function _doRefresh() {
        // Deduplicate concurrent refresh calls
        if (_refreshPromise) {
            return _refreshPromise;
        }

        _refreshPromise = _refreshInternal();
        try {
            return await _refreshPromise;
        } finally {
            _refreshPromise = null;
        }
    }

    async function _refreshInternal() {
        try {
            const resp = await fetch(_config.refreshEndpoint, {
                method: "POST",
                credentials: "same-origin",
            });

            if (!resp.ok) {
                console.warn("[ApplicationGatewayAuth] Refresh failed:", resp.status);
                return false;
            }

            const data = await resp.json();
            if (data.ok && data.expires_at) {
                _scheduleRefreshAt(data.expires_at);
            }

            return data.ok === true;
        } catch (err) {
            console.error("[ApplicationGatewayAuth] Refresh error:", err);
            return false;
        }
    }

    async function _scheduleRefreshFromServer() {
        try {
            const resp = await fetch(_config.tokenInfoEndpoint, {
                credentials: "same-origin",
            });

            if (!resp.ok) return;

            const data = await resp.json();
            if (data.expires_at > 0) {
                _scheduleRefreshAt(data.expires_at, data.server_time);
            }
        } catch (err) {
            console.warn("[ApplicationGatewayAuth] Token info fetch failed:", err);
        }
    }

    function _scheduleRefreshAt(expiresAt, serverTime) {
        if (_refreshTimer) {
            clearTimeout(_refreshTimer);
            _refreshTimer = null;
        }

        const now = serverTime || Math.floor(Date.now() / 1000);
        const secondsUntilExpiry = expiresAt - now;
        const secondsUntilRefresh = secondsUntilExpiry - _config.refreshBeforeExpiry;

        if (secondsUntilRefresh <= 0) {
            // Token already near expiry, refresh immediately
            _doRefresh();
            return;
        }

        const ms = secondsUntilRefresh * 1000;
        console.debug(
            `[ApplicationGatewayAuth] Scheduling refresh in ${Math.round(secondsUntilRefresh / 60)}min`
        );

        _refreshTimer = setTimeout(() => {
            _refreshTimer = null;
            _doRefresh();
        }, ms);
    }

    // ── Expose ─────────────────────────────────────────────────

    return {
        init,
        fetch: authFetch,
        refresh,
    };
})();
