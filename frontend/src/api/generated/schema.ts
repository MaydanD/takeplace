/**
 * AUTO-GENERATED — DO NOT EDIT.
 *
 * Source: frontend/openapi/openapi.json (exported from the FastAPI app).
 * Regenerate with: npm run api:generate
 */
export interface paths {
    "/api/admin/v1/auth/login": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Login
         * @description Authenticate and start a server-side session.
         *
         *     Wrong login and wrong password return the same response (�39.5). Two cheap
         *     rate limits run before the CPU-bound Argon2id verify so the bounded pool
         *     cannot be saturated by garbage logins.
         */
        post: operations["login_api_admin_v1_auth_login_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/admin/v1/auth/logout": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Logout
         * @description Revoke the current session and clear the cookie.
         */
        post: operations["logout_api_admin_v1_auth_logout_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/admin/v1/auth/logout-all": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Logout All
         * @description Revoke every session of this venue, including the current one.
         */
        post: operations["logout_all_api_admin_v1_auth_logout_all_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/admin/v1/me": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Me
         * @description Return the authenticated admin and its venue.
         *
         *     The venue is derived from the session, never from a client-supplied id.
         */
        get: operations["me_api_admin_v1_me_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/admin/v1/settings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Settings
         * @description Return this venue's settings.
         */
        get: operations["get_settings_api_admin_v1_settings_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * Patch Settings
         * @description Update this venue's editable settings.
         *
         *     Only fields the client sent are applied (PATCH semantics); an unknown field
         *     such as ``venue_id`` is dropped by the schema and can never retarget the
         *     mutation (PROJECT-SPEC �7.1).
         */
        patch: operations["patch_settings_api_admin_v1_settings_patch"];
        trace?: never;
    };
    "/health/live": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Live
         * @description Process liveness: no dependency checks.
         */
        get: operations["live_health_live_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/health/ops": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Ops
         * @description Operational health for external monitoring.
         *
         *     Always returns 200 so monitoring can distinguish "degraded" from "down".
         */
        get: operations["ops_health_ops_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/health/ready": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Ready
         * @description Readiness: the app can serve requests and sees a working database.
         */
        get: operations["ready_health_ready_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /**
         * AdminSummary
         * @description The authenticated admin account. Never contains the password hash.
         */
        AdminSummary: {
            /** Id */
            id: number;
            /** Is Active */
            is_active: boolean;
            /** Login */
            login: string;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** LivenessResponse */
        LivenessResponse: {
            /**
             * Status
             * @constant
             */
            status: "ok";
        };
        /** LoginRequest */
        LoginRequest: {
            /** Login */
            login: string;
            /** Password */
            password: string;
        };
        /** MeResponse */
        MeResponse: {
            admin: components["schemas"]["AdminSummary"];
            venue: components["schemas"]["VenueSummary"];
        };
        /** OpsResponse */
        OpsResponse: {
            /**
             * Database
             * @enum {string}
             */
            database: "ok" | "unavailable";
            /** Environment */
            environment: string;
            /**
             * Outbox Unacknowledged Dead
             * @default 0
             */
            outbox_unacknowledged_dead: number;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "degraded";
            /**
             * Timezone Capability
             * @default ok
             * @enum {string}
             */
            timezone_capability: "ok" | "unsupported";
        };
        /** ReadinessResponse */
        ReadinessResponse: {
            /**
             * Database
             * @enum {string}
             */
            database: "ok" | "unavailable";
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /**
         * SettingsUpdate
         * @description Editable venue settings.
         *
         *     ``slug``, ``timezone`` and ``is_active`` are intentionally absent: slug and
         *     timezone are not runtime-editable in v1, and suspension is a CLI operation.
         */
        SettingsUpdate: {
            /** Address */
            address?: string | null;
            /** Name */
            name?: string | null;
            /** Online Booking Enabled */
            online_booking_enabled?: boolean | null;
            /** Phone */
            phone?: string | null;
        };
        /** StatusResponse */
        StatusResponse: {
            /** Status */
            status: string;
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        /**
         * VenueSummary
         * @description Public identity of the tenant the session belongs to.
         */
        VenueSummary: {
            /** Address */
            address: string | null;
            /** Id */
            id: number;
            /** Is Active */
            is_active: boolean;
            /** Name */
            name: string;
            /** Online Booking Enabled */
            online_booking_enabled: boolean;
            /** Phone */
            phone: string | null;
            /** Slug */
            slug: string;
            /** Timezone */
            timezone: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    login_api_admin_v1_auth_login_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LoginRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MeResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    logout_api_admin_v1_auth_logout_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["StatusResponse"];
                };
            };
        };
    };
    logout_all_api_admin_v1_auth_logout_all_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["StatusResponse"];
                };
            };
        };
    };
    me_api_admin_v1_me_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MeResponse"];
                };
            };
        };
    };
    get_settings_api_admin_v1_settings_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["VenueSummary"];
                };
            };
        };
    };
    patch_settings_api_admin_v1_settings_patch: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SettingsUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["VenueSummary"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    live_health_live_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LivenessResponse"];
                };
            };
        };
    };
    ops_health_ops_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OpsResponse"];
                };
            };
        };
    };
    ready_health_ready_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReadinessResponse"];
                };
            };
            /** @description Service Unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReadinessResponse"];
                };
            };
        };
    };
}
