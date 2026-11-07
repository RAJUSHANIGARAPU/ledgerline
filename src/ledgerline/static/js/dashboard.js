// Revenue vs expenses chart. Data arrives in a JSON block; colours come from CSS variables.
window.addEventListener("DOMContentLoaded", () => {
  const canvas = document.getElementById("monthly-chart");
  const block = document.getElementById("chart-data");
  if (!canvas || !block || typeof Chart === "undefined") return;
  const data = JSON.parse(block.textContent);
  const styles = getComputedStyle(document.documentElement);
  const pick = (names, fallback) => {
    for (const name of names) {
      const value = styles.getPropertyValue(name).trim();
      if (value) return value;
    }
    return fallback;
  };
  const revenue = pick(["--chart-1", "--accent", "--primary", "--color-primary"], "#2f6fed");
  const expenses = pick(["--chart-2", "--warn", "--color-warn"], "#d9822b");
  const text = pick(["--text-muted", "--muted", "--text"], "#6b7280");
  const grid = pick(["--border", "--color-border"], "#d1d5db");
  const euros = new Intl.NumberFormat("nl-NL", { style: "currency", currency: "EUR" });
  new Chart(canvas, {
    type: "bar",
    data: {
      labels: data.labels,
      datasets: [
        { label: "Revenue", data: data.revenue.map((c) => c / 100), backgroundColor: revenue },
        { label: "Expenses", data: data.expenses.map((c) => c / 100), backgroundColor: expenses },
      ],
    },
    options: {
      responsive: true,
      animation: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? false : {},
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: text } },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${euros.format(c.parsed.y)}` } },
      },
      scales: {
        x: { ticks: { color: text }, grid: { color: grid } },
        y: { ticks: { color: text, callback: (v) => euros.format(v) }, grid: { color: grid } },
      },
    },
  });
});
