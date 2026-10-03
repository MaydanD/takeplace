"""Pure business logic (PROJECT-SPEC §3.2).

Later stages place shift resolution, ``business_date``, the state machine,
segment rules, capacity and availability overlays here. Stage 1 defines the
package boundary only; the shared 5-minute grid helpers live in ``app.db.time``.
"""
