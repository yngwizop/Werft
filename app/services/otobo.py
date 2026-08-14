from __future__ import annotations

import logging

import httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class OTOBOError(RuntimeError):
    pass


class OTOBOClient:
    """Thin client for OTOBO Generic Interface REST callbacks."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def _base(self) -> str:
        if not self.settings.otobo_url:
            raise OTOBOError("OTOBO_URL is not configured")
        return self.settings.otobo_url.rstrip("/")

    def _gi_url(self, operation: str) -> str:
        return (
            f"{self._base()}/otobo/nph-genericinterface.pl/Webservice/"
            f"{self.settings.otobo_webservice_name}/{operation}"
        )

    def _auth_payload(self) -> dict:
        return {
            "UserLogin": self.settings.otobo_user_login,
            "Password": self.settings.otobo_password,
        }

    def get_ticket(self, ticket_id: str) -> dict:
        payload = {
            **self._auth_payload(),
            "TicketID": ticket_id,
            "DynamicFields": 1,
            "Extended": 1,
        }
        try:
            with httpx.Client(verify=self.settings.otobo_verify_ssl, timeout=30.0) as client:
                resp = client.post(self._gi_url("TicketGet"), json=payload)
            if resp.status_code >= 400:
                raise OTOBOError(f"OTOBO TicketGet failed: {resp.status_code} {resp.text}")
            data = resp.json()
        except OTOBOError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise OTOBOError(f"OTOBO TicketGet error: {exc}") from exc

        if isinstance(data, dict) and data.get("Error"):
            raise OTOBOError(f"OTOBO TicketGet error: {data['Error']}")

        ticket = data.get("Ticket") if isinstance(data, dict) else None
        if isinstance(ticket, list) and ticket:
            ticket = ticket[0]
        if not isinstance(ticket, dict):
            raise OTOBOError(f"OTOBO TicketGet returned no Ticket: {data}")
        return ticket

    def comment_and_set_state(
        self,
        *,
        ticket_id: str,
        body: str,
        state: str,
        subject: str = "Werft",
    ) -> None:
        """Post an article and update ticket state.

        Endpoint shape matches common OTOBO GenericInterface TicketUpdate patterns.
        Adjust path/payload to your webservice mapping if needed.
        """
        url = self._gi_url("TicketUpdate")
        payload = {
            "UserLogin": self.settings.otobo_user_login,
            "Password": self.settings.otobo_password,
            "TicketID": ticket_id,
            "Ticket": {"State": state},
            "Article": {
                "Subject": subject,
                "Body": body,
                "ContentType": "text/plain; charset=utf-8",
                "CommunicationChannel": "Internal",
                "SenderType": "agent",
            },
        }
        try:
            with httpx.Client(verify=self.settings.otobo_verify_ssl, timeout=30.0) as client:
                resp = client.post(url, json=payload)
            if resp.status_code >= 400:
                raise OTOBOError(f"OTOBO TicketUpdate failed: {resp.status_code} {resp.text}")
            logger.info("OTOBO ticket %s updated -> state=%s", ticket_id, state)
        except OTOBOError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise OTOBOError(f"OTOBO callback error: {exc}") from exc

    def notify_provisioning(self, ticket_id: str, job_id: str) -> None:
        self.comment_and_set_state(
            ticket_id=ticket_id,
            state=self.settings.otobo_status_provisioning,
            subject="Werft started",
            body=f"Provisioning job {job_id} accepted. NetBox IP reservation and hypervisor clone in progress.",
        )

    def notify_success(
        self,
        ticket_id: str,
        *,
        hostname: str,
        ip: str,
        hypervisor_ref: str,
        node: str = "",
        hypervisor: str = "",
    ) -> None:
        lines = [
            "VM successfully provisioned.",
            f"Hostname: {hostname}",
            f"IP: {ip}",
        ]
        if hypervisor:
            lines.append(f"Hypervisor: {hypervisor}")
        if node:
            lines.append(f"Ziel: {node}")
        if hypervisor_ref:
            lines.append(f"Hypervisor ref: {hypervisor_ref}")
        self.comment_and_set_state(
            ticket_id=ticket_id,
            state=self.settings.otobo_status_done,
            subject="Werft completed",
            body="\n".join(lines) + "\n",
        )

    def notify_failure(self, ticket_id: str, error: str) -> None:
        self.comment_and_set_state(
            ticket_id=ticket_id,
            state=self.settings.otobo_status_failed,
            subject="Werft failed",
            body=f"Provisioning failed.\n\nError:\n{error}\n\nReserved resources were rolled back where possible.",
        )
