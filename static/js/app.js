document.addEventListener("DOMContentLoaded", () => {
    const diffModal = document.getElementById("diffModal");

    if (!diffModal) {
        return;
    }

    diffModal.addEventListener("show.bs.modal", (event) => {
        const trigger = event.relatedTarget;
        if (!trigger) {
            return;
        }

        document.getElementById("diffModalSummary").textContent =
            trigger.getAttribute("data-summary") || "";
        document.getElementById("diffModalDate").textContent =
            trigger.getAttribute("data-date") || "";
        document.getElementById("diffModalContent").textContent =
            trigger.getAttribute("data-diff") || "Aucun détail disponible.";
    });
});
