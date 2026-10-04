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
         *     Wrong login and wrong password return the same response (§39.5). Two cheap
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
    "/api/admin/v1/bookings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Bookings
         * @description Cursor/limit list with the §35 filters (newest first).
         */
        get: operations["get_bookings_api_admin_v1_bookings_get"];
        put?: never;
        /**
         * Post Booking
         * @description Create a manual booking idempotently (§19, §32.2).
         *
         *     A fresh booking is ``201``; a replay of the same key+payload is ``200`` with
         *     the same booking identity and no new side effects (§36).
         */
        post: operations["post_booking_api_admin_v1_bookings_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/admin/v1/bookings/unresolved": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Unresolved
         * @description NEW/WAITING bookings whose planned interval has already ended (§17.3).
         */
        get: operations["get_unresolved_api_admin_v1_bookings_unresolved_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/admin/v1/bookings/{booking_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Booking Detail
         * @description Return one booking of this venue, or 404 for any other tenant (§7.1).
         */
        get: operations["get_booking_detail_api_admin_v1_bookings__booking_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/admin/v1/bookings/{booking_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Post Cancel Booking
         * @description Cancel an unopened booking; deactivates its occupancies (§9, §13).
         */
        post: operations["post_cancel_booking_api_admin_v1_bookings__booking_id__cancel_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/admin/v1/bookings/{booking_id}/change-time": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Post Change Time
         * @description Reschedule a NEW/WAITING booking and rewrite its shift snapshot (§5.5).
         */
        post: operations["post_change_time_api_admin_v1_bookings__booking_id__change_time_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/admin/v1/bookings/{booking_id}/history": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Booking History
         * @description Return the append-only history ordered by ``booking_events.id`` (§6.10).
         */
        get: operations["get_booking_history_api_admin_v1_bookings__booking_id__history_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/admin/v1/halls": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Halls
         * @description List this venue's halls (archived halls are hidden unless requested).
         */
        get: operations["get_halls_api_admin_v1_halls_get"];
        put?: never;
        /**
         * Post Hall
         * @description Create a hall (canvas) for this venue.
         */
        post: operations["post_hall_api_admin_v1_halls_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/admin/v1/halls/{hall_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Hall Detail
         * @description Return one hall with its tables (including archived) and static elements.
         */
        get: operations["get_hall_detail_api_admin_v1_halls__hall_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * Patch Hall
         * @description Update a hall's name, canvas size or operational bookability.
         *
         *     A canvas-size change is a layout-owned edit and bumps ``layout_revision``;
         *     ``is_bookable`` never does (§31).
         */
        patch: operations["patch_hall_api_admin_v1_halls__hall_id__patch"];
        trace?: never;
    };
    "/api/admin/v1/halls/{hall_id}/archive": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Post Archive Hall
         * @description Archive a hall; blocked while it still has non-archived tables (§29.5).
         */
        post: operations["post_archive_hall_api_admin_v1_halls__hall_id__archive_post"];
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
         *     expressed as ``close_time < open_time`` (§5.2). A schedule whose adjacent
         *     shifts would overlap is rejected with ``SCHEDULE_OVERLAP`` (§5.4).
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
         * @description Return the computed state for a business date (§5.1, §5.6).
         *
         *     With no ``business_date`` the *current* business date is used — which may be
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
         * @description Create or replace the exception for one business date (§5.3).
         */
        put: operations["put_exception_api_admin_v1_schedule_exceptions__business_date__put"];
        post?: never;
        /**
         * Remove Exception
         * @description Delete the exception for one date, reverting to the weekly rule (§5.3).
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
         *     mutation (PROJECT-SPEC §7.1).
         */
        patch: operations["patch_settings_api_admin_v1_settings_patch"];
        trace?: never;
    };
    "/api/admin/v1/system/status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** System Status */
        get: operations["system_status_api_admin_v1_system_status_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/admin/v1/tables": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Tables
         * @description Flat list of this venue's tables for the list view, with hall names.
         */
        get: operations["get_tables_api_admin_v1_tables_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/admin/v1/tables/{table_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * Patch Table
         * @description Operational ``is_bookable`` toggle; geometry is owned by layout-save (§35).
         */
        patch: operations["patch_table_api_admin_v1_tables__table_id__patch"];
        trace?: never;
    };
    "/api/admin/v1/tables/{table_id}/archive": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Post Archive Table
         * @description Archive a table; the live/future-occupancy guard lands with Stage 5 (§29.3).
         */
        post: operations["post_archive_table_api_admin_v1_tables__table_id__archive_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/public/v1/venues/{slug}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Public Venue
         * @description Return only public venue/hall/table data for one tenant (§34).
         */
        get: operations["get_public_venue_api_public_v1_venues__slug__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/public/v1/venues/{slug}/availability": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Public Availability
         * @description Return a read-only availability snapshot for one venue/date selection (§16, §34).
         */
        get: operations["get_public_availability_api_public_v1_venues__slug__availability_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/public/v1/venues/{slug}/bookings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Post Public Booking
         * @description Create a public ONLINE booking idempotently (§18, §18.2, §32.2).
         *
         *     The client must send:
         *
         *     * ``Idempotency-Key``: a UUID generated once per user action and reused for
         *       network retries of the same action;
         *     * ``honeypot``: must be empty for a normal user (§40, §50).
         *
         *     The server follows the §18.2 ordering: cheap transport guards, slug resolve,
         *     CAPTCHA hook, soft rate limit, then delegation to the booking core which does
         *     the idempotency lookup before mutable gates, the kill-switch final gate, and
         *     the transactional create under the canonical lock order.
         */
        post: operations["post_public_booking_api_public_v1_venues__slug__bookings_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
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
        /** BarElement */
        BarElement: {
            /** Height */
            height: number;
            /** Label */
            label?: string | null;
            /**
             * Rotation
             * @default 0
             */
            rotation: number;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "bar";
            /** Width */
            width: number;
            /** X */
            x: number;
            /** Y */
            y: number;
            /**
             * Z Index
             * @default 0
             */
            z_index: number;
        };
        /**
         * BookingCancel
         * @description Cancel an unopened booking (§9, §11).
         */
        BookingCancel: {
            /** Expected Version */
            expected_version: number;
            /** Note */
            note?: string | null;
            /**
             * Reason
             * @enum {string}
             */
            reason: "GUEST_CANCELED" | "NO_SHOW" | "DUPLICATE" | "UNREACHABLE" | "RESCHEDULED" | "GUEST_LATE" | "CREATION_ERROR" | "TERMS_REFUSED" | "INVALID_DATA" | "MOVED_ELSEWHERE" | "NO_TABLES" | "VENUE_CLOSED" | "ENTRY_REFUSED" | "OTHER";
        };
        /**
         * BookingChangeTime
         * @description Move a NEW/WAITING booking to a new interval (§5.5, §32.3).
         */
        BookingChangeTime: {
            /**
             * Ends At
             * Format: date-time
             */
            ends_at: string;
            /** Expected Version */
            expected_version: number;
            /**
             * Starts At
             * Format: date-time
             */
            starts_at: string;
        };
        /**
         * BookingCreate
         * @description Admin manual create (§19). ``ONLINE`` is reserved for the public flow (§8).
         *
         *     Times are absolute ISO-8601 instants; the backend derives the business date
         *     and the shift snapshot from the canonical schedule resolver.
         */
        BookingCreate: {
            /**
             * Ends At
             * Format: date-time
             */
            ends_at: string;
            /** Guest Comment */
            guest_comment?: string | null;
            /** Guest Name */
            guest_name: string;
            /** Guest Phone Raw */
            guest_phone_raw?: string | null;
            /** Party Size */
            party_size: number;
            /**
             * Source
             * @enum {string}
             */
            source: "PHONE" | "VK" | "WALK_IN" | "OTHER";
            /**
             * Starts At
             * Format: date-time
             */
            starts_at: string;
            /** Table Ids */
            table_ids: number[];
        };
        /**
         * BookingEventSummary
         * @description One append-only history row; payload holds no PII (§6.10).
         */
        BookingEventSummary: {
            /** Actor Type */
            actor_type: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Event Type */
            event_type: string;
            /** Id */
            id: number;
            /** Payload */
            payload: {
                [key: string]: unknown;
            };
        };
        /** BookingHistoryResponse */
        BookingHistoryResponse: {
            /** Events */
            events: components["schemas"]["BookingEventSummary"][];
        };
        /** BookingListResponse */
        BookingListResponse: {
            /** Items */
            items: components["schemas"]["BookingSummary"][];
            /** Next Cursor */
            next_cursor?: number | null;
        };
        /**
         * BookingSummary
         * @description Booking representation for the admin book (no idempotency keys/HMACs).
         */
        BookingSummary: {
            /**
             * Business Date
             * Format: date
             */
            business_date: string;
            /** Canceled At */
            canceled_at: string | null;
            /** Cancellation Reason */
            cancellation_reason: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Ends At
             * Format: date-time
             */
            ends_at: string;
            /** Guest Comment */
            guest_comment: string | null;
            /** Guest Name */
            guest_name: string | null;
            /** Guest Phone Normalized */
            guest_phone_normalized: string | null;
            /** Guest Phone Raw */
            guest_phone_raw: string | null;
            /** Id */
            id: number;
            /** Number */
            number: number;
            /** Party Size */
            party_size: number;
            /**
             * Shift Ends At
             * Format: date-time
             */
            shift_ends_at: string;
            /**
             * Shift Starts At
             * Format: date-time
             */
            shift_starts_at: string;
            /** Source */
            source: string;
            /**
             * Starts At
             * Format: date-time
             */
            starts_at: string;
            /** Status */
            status: string;
            /** Table Ids */
            table_ids: number[];
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Venue Id */
            venue_id: number;
            /** Version */
            version: number;
        };
        /**
         * BusinessDayResponse
         * @description Computed schedule state for one business date (PROJECT-SPEC §5.1, §5.6).
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
        /** HallCreate */
        HallCreate: {
            /**
             * Canvas Height
             * @default 800
             */
            canvas_height: number;
            /**
             * Canvas Width
             * @default 1200
             */
            canvas_width: number;
            /**
             * Is Bookable
             * @default true
             */
            is_bookable: boolean;
            /** Name */
            name: string;
        };
        /**
         * HallDetail
         * @description A hall with its tables and static elements (read-only canvas source).
         */
        HallDetail: {
            /** Archived At */
            archived_at: string | null;
            /** Canvas Height */
            canvas_height: number;
            /** Canvas Width */
            canvas_width: number;
            /** Id */
            id: number;
            /** Is Bookable */
            is_bookable: boolean;
            /** Layout Revision */
            layout_revision: number;
            /** Name */
            name: string;
            /** Static Elements */
            static_elements: (components["schemas"]["WallElement"] | components["schemas"]["StageElement"] | components["schemas"]["BarElement"] | components["schemas"]["ZoneElement"] | components["schemas"]["TextElement"])[];
            /** Tables */
            tables: components["schemas"]["TableSummary"][];
        };
        /**
         * HallSummary
         * @description Hall metadata (canvas size, revision, archive state).
         */
        HallSummary: {
            /** Archived At */
            archived_at: string | null;
            /** Canvas Height */
            canvas_height: number;
            /** Canvas Width */
            canvas_width: number;
            /** Id */
            id: number;
            /** Is Bookable */
            is_bookable: boolean;
            /** Layout Revision */
            layout_revision: number;
            /** Name */
            name: string;
            /** Table Count */
            table_count: number;
        };
        /**
         * HallUpdate
         * @description Editable hall fields. ``is_bookable`` does not bump the layout revision.
         */
        HallUpdate: {
            /** Canvas Height */
            canvas_height?: number | null;
            /** Canvas Width */
            canvas_width?: number | null;
            /** Is Bookable */
            is_bookable?: boolean | null;
            /** Name */
            name?: string | null;
        };
        /** HallsResponse */
        HallsResponse: {
            /** Halls */
            halls: components["schemas"]["HallSummary"][];
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
             * Online Abuse Alerts
             * @default 0
             */
            online_abuse_alerts: number;
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
        /**
         * PublicAvailabilityResponse
         * @description Read-only availability snapshot (§16, §34).
         */
        PublicAvailabilityResponse: {
            /**
             * Business Date
             * Format: date
             */
            business_date: string;
            /** Is Open */
            is_open: boolean;
            /** Shift End */
            shift_end: string | null;
            /** Shift Start */
            shift_start: string | null;
            /** Tables */
            tables: components["schemas"]["PublicAvailabilityTable"][];
            /** Venue Timezone */
            venue_timezone: string;
        };
        /** PublicAvailabilitySlot */
        PublicAvailabilitySlot: {
            /** Earliest End */
            earliest_end: string;
            /** End Options */
            end_options: string[];
            /** Latest End */
            latest_end: string;
            /** Start */
            start: string;
        };
        /** PublicAvailabilityTable */
        PublicAvailabilityTable: {
            /** Capacity */
            capacity: number;
            /** Hall Id */
            hall_id: number;
            /** Hall Name */
            hall_name: string;
            /** Id */
            id: number;
            /** Number */
            number: string;
            /** Slots */
            slots: components["schemas"]["PublicAvailabilitySlot"][];
        };
        /**
         * PublicCreateRequest
         * @description Public ONLINE booking create payload (§18, §42).
         *
         *     The ``honeypot`` field is hidden in the UI and must be empty for a normal
         *     user (§40, §50). ``captcha_token`` is only required when the CAPTCHA feature
         *     flag is enabled.
         */
        PublicCreateRequest: {
            /** Captcha Token */
            captcha_token?: string | null;
            /**
             * Ends At
             * Format: date-time
             */
            ends_at: string;
            /** Guest Comment */
            guest_comment?: string | null;
            /** Guest Name */
            guest_name: string;
            /** Guest Phone Raw */
            guest_phone_raw: string;
            /** Honeypot */
            honeypot?: string | null;
            /** Party Size */
            party_size: number;
            /** Privacy Policy Version */
            privacy_policy_version: string;
            /**
             * Starts At
             * Format: date-time
             */
            starts_at: string;
            /** Table Id */
            table_id: number;
        };
        /**
         * PublicCreateResponse
         * @description Public booking confirmation (§50).
         *
         *     Shows only what the success screen needs: booking number, venue, date, time,
         *     hall, table, party size. No guest name/phone/comment is returned to avoid
         *     leaking PII back through the public response (§36, §42).
         */
        PublicCreateResponse: {
            /**
             * Business Date
             * Format: date
             */
            business_date: string;
            /**
             * Ends At
             * Format: date-time
             */
            ends_at: string;
            /** Id */
            id: number;
            /** Number */
            number: number;
            /** Party Size */
            party_size: number;
            /**
             * Shift Ends At
             * Format: date-time
             */
            shift_ends_at: string;
            /**
             * Shift Starts At
             * Format: date-time
             */
            shift_starts_at: string;
            /** Source */
            source: string;
            /**
             * Starts At
             * Format: date-time
             */
            starts_at: string;
            /** Table Ids */
            table_ids: number[];
        };
        /**
         * PublicHall
         * @description Public hall with canvas geometry, static elements and active tables.
         */
        PublicHall: {
            /** Canvas Height */
            canvas_height: number;
            /** Canvas Width */
            canvas_width: number;
            /** Id */
            id: number;
            /** Is Bookable */
            is_bookable: boolean;
            /** Name */
            name: string;
            /** Static Elements */
            static_elements: (components["schemas"]["WallElement"] | components["schemas"]["StageElement"] | components["schemas"]["BarElement"] | components["schemas"]["ZoneElement"] | components["schemas"]["TextElement"])[];
            /** Tables */
            tables: components["schemas"]["PublicTable"][];
        };
        /**
         * PublicTable
         * @description Public table geometry (no archive/internal fields, §34).
         */
        PublicTable: {
            /** Capacity */
            capacity: number;
            /** Hall Id */
            hall_id: number;
            /** Height */
            height: number;
            /** Id */
            id: number;
            /** Number */
            number: string;
            /** Rotation */
            rotation: number;
            /** Shape */
            shape: string;
            /** Width */
            width: number;
            /** X */
            x: number;
            /** Y */
            y: number;
            /** Z Index */
            z_index: number;
        };
        /**
         * PublicVenueResponse
         * @description Public venue data for the booking UI (§34, §50).
         */
        PublicVenueResponse: {
            /** Halls */
            halls: components["schemas"]["PublicHall"][];
            /** Is Active */
            is_active: boolean;
            /** Name */
            name: string;
            /** Online Booking Enabled */
            online_booking_enabled: boolean;
            /** Privacy Policy Version */
            privacy_policy_version: string;
            /** Slug */
            slug: string;
            /** Timezone */
            timezone: string;
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
         * @description A date-specific override; it fully replaces the weekly rule (§5.3).
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
        /** StageElement */
        StageElement: {
            /** Height */
            height: number;
            /** Label */
            label?: string | null;
            /**
             * Rotation
             * @default 0
             */
            rotation: number;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "stage";
            /** Width */
            width: number;
            /** X */
            x: number;
            /** Y */
            y: number;
            /**
             * Z Index
             * @default 0
             */
            z_index: number;
        };
        /** StatusResponse */
        StatusResponse: {
            /** Status */
            status: string;
        };
        /** SystemStatusResponse */
        SystemStatusResponse: {
            /** Online Abuse Alert */
            online_abuse_alert: boolean;
        };
        /**
         * TableSummary
         * @description Table geometry and state (PROJECT-SPEC §6.5).
         */
        TableSummary: {
            /** Archived At */
            archived_at: string | null;
            /** Capacity */
            capacity: number;
            /** Hall Id */
            hall_id: number;
            /** Hall Name */
            hall_name?: string | null;
            /** Height */
            height: number;
            /** Id */
            id: number;
            /** Is Bookable */
            is_bookable: boolean;
            /** Number */
            number: string;
            /** Rotation */
            rotation: number;
            /** Shape */
            shape: string;
            /** Width */
            width: number;
            /** X */
            x: number;
            /** Y */
            y: number;
            /** Z Index */
            z_index: number;
        };
        /**
         * TableUpdate
         * @description Operational table toggle. Geometry is owned by layout-save, not here (§35).
         */
        TableUpdate: {
            /** Is Bookable */
            is_bookable: boolean;
        };
        /** TablesResponse */
        TablesResponse: {
            /** Tables */
            tables: components["schemas"]["TableSummary"][];
        };
        /** TextElement */
        TextElement: {
            /**
             * Font Size
             * @default 14
             */
            font_size: number;
            /**
             * Rotation
             * @default 0
             */
            rotation: number;
            /** Text */
            text: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "text";
            /** X */
            x: number;
            /** Y */
            y: number;
            /**
             * Z Index
             * @default 0
             */
            z_index: number;
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
        /** WallElement */
        WallElement: {
            /** Height */
            height: number;
            /**
             * Rotation
             * @default 0
             */
            rotation: number;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "wall";
            /** Width */
            width: number;
            /** X */
            x: number;
            /** Y */
            y: number;
            /**
             * Z Index
             * @default 0
             */
            z_index: number;
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
        /** ZoneElement */
        ZoneElement: {
            /** Height */
            height: number;
            /** Label */
            label?: string | null;
            /**
             * Rotation
             * @default 0
             */
            rotation: number;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "zone";
            /** Width */
            width: number;
            /** X */
            x: number;
            /** Y */
            y: number;
            /**
             * Z Index
             * @default 0
             */
            z_index: number;
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
    get_bookings_api_admin_v1_bookings_get: {
        parameters: {
            query?: {
                business_date?: string | null;
                status?: string | null;
                source?: string | null;
                table_id?: number | null;
                phone?: string | null;
                cursor?: number | null;
                limit?: number;
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
                    "application/json": components["schemas"]["BookingListResponse"];
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
    post_booking_api_admin_v1_bookings_post: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["BookingCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BookingSummary"];
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
    get_unresolved_api_admin_v1_bookings_unresolved_get: {
        parameters: {
            query?: {
                limit?: number;
                cursor?: number | null;
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
                    "application/json": components["schemas"]["BookingListResponse"];
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
    get_booking_detail_api_admin_v1_bookings__booking_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                booking_id: number;
            };
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
                    "application/json": components["schemas"]["BookingSummary"];
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
    post_cancel_booking_api_admin_v1_bookings__booking_id__cancel_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                booking_id: number;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["BookingCancel"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BookingSummary"];
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
    post_change_time_api_admin_v1_bookings__booking_id__change_time_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                booking_id: number;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["BookingChangeTime"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BookingSummary"];
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
    get_booking_history_api_admin_v1_bookings__booking_id__history_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                booking_id: number;
            };
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
                    "application/json": components["schemas"]["BookingHistoryResponse"];
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
    get_halls_api_admin_v1_halls_get: {
        parameters: {
            query?: {
                include_archived?: boolean;
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
                    "application/json": components["schemas"]["HallsResponse"];
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
    post_hall_api_admin_v1_halls_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["HallCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HallSummary"];
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
    get_hall_detail_api_admin_v1_halls__hall_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                hall_id: number;
            };
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
                    "application/json": components["schemas"]["HallDetail"];
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
    patch_hall_api_admin_v1_halls__hall_id__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                hall_id: number;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["HallUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HallSummary"];
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
    post_archive_hall_api_admin_v1_halls__hall_id__archive_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                hall_id: number;
            };
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
                    "application/json": components["schemas"]["HallSummary"];
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
            query?: {
                confirm?: boolean;
            };
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
            query?: {
                confirm?: boolean;
            };
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
            query?: {
                confirm?: boolean;
            };
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
    system_status_api_admin_v1_system_status_get: {
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
                    "application/json": components["schemas"]["SystemStatusResponse"];
                };
            };
        };
    };
    get_tables_api_admin_v1_tables_get: {
        parameters: {
            query?: {
                hall_id?: number | null;
                include_archived?: boolean;
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
                    "application/json": components["schemas"]["TablesResponse"];
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
    patch_table_api_admin_v1_tables__table_id__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                table_id: number;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TableUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TableSummary"];
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
    post_archive_table_api_admin_v1_tables__table_id__archive_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                table_id: number;
            };
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
                    "application/json": components["schemas"]["TableSummary"];
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
    get_public_venue_api_public_v1_venues__slug__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                slug: string;
            };
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
                    "application/json": components["schemas"]["PublicVenueResponse"];
                };
            };
            /** @description venue not found */
            404: {
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
    get_public_availability_api_public_v1_venues__slug__availability_get: {
        parameters: {
            query?: {
                business_date?: string | null;
                hall_id?: number | null;
                table_id?: number | null;
                party_size?: number;
            };
            header?: never;
            path: {
                slug: string;
            };
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
                    "application/json": components["schemas"]["PublicAvailabilityResponse"];
                };
            };
            /** @description venue not found */
            404: {
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
            /** @description rate limited */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    post_public_booking_api_public_v1_venues__slug__bookings_post: {
        parameters: {
            query?: never;
            header?: {
                "Idempotency-Key"?: string;
            };
            path: {
                slug: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PublicCreateRequest"];
            };
        };
        responses: {
            /** @description idempotent replay */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PublicCreateResponse"];
                };
            };
            /** @description new booking */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description missing or invalid Idempotency-Key / honeypot / captcha */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description venue not found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description idempotency key reused, booking conflict, or online disabled */
            409: {
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
            /** @description rate limited */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description service unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
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
