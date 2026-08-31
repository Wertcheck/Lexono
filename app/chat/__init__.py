"""Chat – zentrale Arbeitsoberflaeche des Dashboards (UI-Ueberarbeitung).

Bewusst KEINE neue KI-/Cloud-Architektur: `ChatService` ist eine duenne
Fassade um den bereits bestehenden, vollstaendig getesteten Weg
`DraftingService.create_draft` (app/drafting/service.py) - identischer
Privacy-Gateway-/Pseudonymisierungs-/Final-Payload-Gate-Durchlauf wie beim
Schriftsatz-Generator (app/web/schriftsatz_router.py), nur mit einer
Chat-Oberflaeche davor statt eines Formulars."""

from app.chat.service import ChatService

__all__ = ["ChatService"]
