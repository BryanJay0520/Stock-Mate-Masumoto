// Shared data loading + inventory presentation rules. Status colors and
// confidence explanations are computed here so every page agrees on them.

window.SM = window.SM || {};

SM.data = {
  async catalog() {
    const [stock, ingredients, suppliers] = await Promise.all([
      SM.api.get("/inventory/stock"),
      SM.api.get("/ingredients"),
      SM.api.get("/suppliers"),
    ]);
    const ingredientById = Object.fromEntries(ingredients.map((i) => [i.id, i]));
    const supplierById = Object.fromEntries(suppliers.map((s) => [s.id, s]));
    const rows = stock.map((item) => ({
      ...item,
      ingredient: ingredientById[item.ingredient_id] || null,
      supplier: item.ingredient_id in ingredientById
        ? supplierById[ingredientById[item.ingredient_id].primary_supplier_id] || null
        : null,
    }));
    return { stock, ingredients, suppliers, ingredientById, supplierById, rows };
  },

  // Stock AVAILABILITY (supply) — independent from data confidence.
  stockStatus(item) {
    if (!item.ingredient) return { kind: "neutral", label: "No data" };
    if (!item.last_transaction_at) return { kind: "neutral", label: "No data" };
    const qty = SM.ui.toMillis(item.current_quantity);
    const safety = SM.ui.toMillis(item.ingredient.min_safety_stock);
    const critical = SM.ui.toMillis(item.ingredient.critical_threshold);
    if (qty === null) return { kind: "neutral", label: "No data" };
    if (critical !== null && qty <= critical) return { kind: "critical", label: "Critical" };
    if (safety !== null && qty <= safety) return { kind: "warning", label: "Low stock" };
    return { kind: "safe", label: "Safe" };
  },

  // Stock CONFIDENCE (data freshness) — a separate badge, per requirements.
  confidenceInfo(item) {
    const counted = item.last_counted_at
      ? SM.ui.formatDate(item.last_counted_at)
      : null;
    if (item.confidence === "high") {
      return { kind: "safe", label: "High confidence", detail: `Physically counted ${counted}` };
    }
    if (item.confidence === "medium") {
      return {
        kind: "warning", label: "Medium confidence",
        detail: `Last physical count ${counted} — verification becoming outdated`,
      };
    }
    return {
      kind: "critical", label: "Low confidence",
      detail: counted
        ? `Last physical count ${counted} — outdated or unresolved discrepancies`
        : "No verified physical stock count recorded",
    };
  },

  stockStatusBadge(item) {
    const status = this.stockStatus(item);
    return SM.ui.badge(status.label, status.kind);
  },

  confidenceBadge(item) {
    const info = this.confidenceInfo(item);
    return SM.ui.badge(info.label, info.kind, info.detail);
  },
};
