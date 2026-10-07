"use client";

import { Line, LineChart, Tooltip, YAxis } from "recharts";

/** 30-day composite trend. Single series, so no legend; hover shows the value. */
export function Sparkline({ values, width = 96, height = 28 }: { values: number[]; width?: number; height?: number }) {
  if (values.length < 2) {
    return (
      <span className="text-xs text-muted-foreground" style={{ width, display: "inline-block" }}>
        {values.length === 1 ? "1 day" : "—"}
      </span>
    );
  }
  const data = values.map((v, i) => ({ i, v: Math.round(v * 100) }));
  return (
    <div style={{ width, height }} aria-label={`composite trend, ${data[0].v} to ${data[data.length - 1].v}`}>
      <LineChart width={width} height={height} data={data} margin={{ top: 3, right: 3, bottom: 3, left: 3 }}>
          <YAxis hide domain={["dataMin - 2", "dataMax + 2"]} />
          <Tooltip
            cursor={{ stroke: "var(--border)" }}
            contentStyle={{
              background: "var(--card)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              fontSize: 11,
              padding: "2px 6px",
              color: "var(--foreground)",
            }}
            labelFormatter={(i) => `${values.length - 1 - Number(i)}d ago`}
            formatter={(v) => [v, "score"]}
          />
          <Line
            type="monotone"
            dataKey="v"
            stroke="var(--accent)"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4, stroke: "var(--card)", strokeWidth: 2 }}
            isAnimationActive={false}
          />
      </LineChart>
    </div>
  );
}
