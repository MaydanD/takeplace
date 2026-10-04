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
    "/api/admin/v1/schedule": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Weekly Schedule
         * @description Return this venue's weekly schedule (all seven weekdays).
         */
        get: operations["get_weekly_schedule_api_admin_v1_schedule_get"];
        /**
         * Put Weekly Schedule
         * @description Replace the weekly schedule, validating grid rules and adjacent overlaps.
         *
         *     The request must define every weekday; a shift that crosses midnight is
         *     expressed as ``close_time < open_time`` (�5.2). A schedule whose adjacent
         *     shifts would overlap is rejected with ``SCHEDULE_OVERLAP`` (�5.4).
         */
        put: operations["put_weekly_schedule_api_admin_v1_schedule_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/admin/v1/schedule/business-day": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Business Day
         * @description Return the computed state for a business date (�5.1, �5.6).
         *
         *     With no ``business_date`` the *current* business date is used � which may be
         *     yesterday's date while an overnight shift is still running.
         */
        get: operations["get_business_day_api_admin_v1_schedule_business_day_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/admin/v1/schedule/exceptions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Exceptions
         * @description Return this venue's date-specific schedule exceptions.
         */
        get: operations["get_exceptions_api_admin_v1_schedule_exceptions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/admin/v1/schedule/exceptions/{business_date}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Put Exception
         * @description Create or replace the exception for one business date (�5.3).
         */
        put: operations["put_exception_api_admin_v1_schedule_exceptions__business_date__put"];
        post?: never;
        /**
         * Remove Exception
         * @description Delete the exception for one date, reverting to the weekly rule (�5.3).
         */
        delete: operations["remove_exception_api_admin_v1_schedule_exceptions__business_date__delete"];
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
        /**
         * BusinessDayResponse
         * @description Computed schedule state for one business date (PROJECT-SPEC �5.1, �5.6).
         */
        BusinessDayResponse: {
            /**
             * Business Date
             * Format: date
             */
            business_date: string;
            /**
             * Current Business Date
             * Format: date
             */
            current_business_date: string;
            /** Is Open */
            is_open: boolean;
            /** Is Open Now */
            is_open_now: boolean;
            /** Shift End */
            shift_end: string | null;
            /** Shift Start */
            shift_start: string | null;
            /** Timezone */
            timezone: string;
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
         * ScheduleDay
         * @description One weekday rule. ``is_open=false`` means the day is closed.
         */
        ScheduleDay: {
            /** Close Time */
            close_time?: string | null;
            /** Is Open */
            is_open: boolean;
            /** Open Time */
            open_time?: string | null;
            /** Weekday */
            weekday: number;
        };
        /** ScheduleExceptionEntry */
        ScheduleExceptionEntry: {
            /** Close Time */
            close_time: string | null;
            /**
             * Date
             * Format: date
             */
            date: string;
            /** Is Closed */
            is_closed: boolean;
            /** Open Time */
            open_time: string | null;
        };
        /**
         * ScheduleExceptionUpdate
         * @description A date-specific override; it fully replaces the weekly rule (�5.3).
         */
        ScheduleExceptionUpdate: {
            /** Close Time */
            close_time?: string | null;
            /** Is Closed */
            is_closed: boolean;
            /** Open Time */
            open_time?: string | null;
        };
        /** ScheduleExceptionsResponse */
        ScheduleExceptionsResponse: {
            /** Exceptions */
            exceptions: components["schemas"]["ScheduleExceptionEntry"][];
            /** Timezone */
            timezone: string;
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
        /** WeeklyScheduleResponse */
        WeeklyScheduleResponse: {
            /** Timezone */
            timezone: string;
            /** Weekdays */
            weekdays: components["schemas"]["ScheduleDay"][];
        };
        /**
         * WeeklyScheduleUpdate
         * @description Full replacement of the weekly schedule (all seven weekdays).
         */
        WeeklyScheduleUpdate: {
            /** Weekdays */
            weekdays: components["schemas"]["ScheduleDay"][];
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
    get_weekly_schedule_api_admin_v1_schedule_get: {
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
                    "application/json": components["schemas"]["WeeklyScheduleResponse"];
                };
            };
        };
    };
    put_weekly_schedule_api_admin_v1_schedule_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WeeklyScheduleUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WeeklyScheduleResponse"];
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
    get_business_day_api_admin_v1_schedule_business_day_get: {
        parameters: {
            query?: {
                business_date?: string | null;
            };
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
                    "application/json": components["schemas"]["BusinessDayResponse"];
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
    get_exceptions_api_admin_v1_schedule_exceptions_get: {
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
                    "application/json": components["schemas"]["ScheduleExceptionsResponse"];
                };
            };
        };
    };
    put_exception_api_admin_v1_schedule_exceptions__business_date__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                business_date: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ScheduleExceptionUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ScheduleExceptionEntry"];
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
    remove_exception_api_admin_v1_schedule_exceptions__business_date__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                business_date: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
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
