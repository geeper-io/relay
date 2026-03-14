import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";
import tailwind from "@astrojs/tailwind";

export default defineConfig({
  integrations: [
    starlight({
      title: "LLM Proxy",
      description: "Enterprise AI gateway — OpenAI & Anthropic compatible.",
      customCss: ["./src/styles/docs.css"],
      sidebar: [
        {
          label: "Getting Started",
          items: [
            { label: "Quickstart", slug: "docs/getting-started/quickstart" },
            { label: "First API Key", slug: "docs/getting-started/first-api-key" },
            { label: "Configuration", slug: "docs/getting-started/configuration" },
            { label: "Kubernetes", slug: "docs/getting-started/kubernetes" },
          ],
        },
        {
          label: "API Reference",
          items: [
            { label: "Overview", slug: "docs/api-reference/overview" },
            { label: "Chat Completions", slug: "docs/api-reference/chat-completions" },
            { label: "Messages", slug: "docs/api-reference/messages" },
            { label: "Models", slug: "docs/api-reference/models" },
            { label: "Health", slug: "docs/api-reference/health" },
          ],
        },
        {
          label: "Features",
          items: [
            { label: "Pipeline", slug: "docs/features/pipeline" },
            { label: "Rate Limiting", slug: "docs/features/rate-limiting" },
            { label: "PII Scrubbing", slug: "docs/features/pii-scrubbing" },
            { label: "Content Policy", slug: "docs/features/content-policy" },
            { label: "RAG", slug: "docs/features/rag" },
            { label: "Caching", slug: "docs/features/caching" },
            { label: "Analytics", slug: "docs/features/analytics" },
          ],
        },
        {
          label: "Admin",
          items: [
            { label: "Teams & Keys", slug: "docs/admin/teams-and-keys" },
            { label: "Knowledge Base", slug: "docs/admin/knowledge-base" },
            { label: "Usage Reporting", slug: "docs/admin/usage-reporting" },
            { label: "Google SSO", slug: "docs/admin/google-sso" },
          ],
        },
        {
          label: "Deployment",
          items: [
            { label: "Helm Reference", slug: "docs/deployment/helm-reference" },
            { label: "Scaling", slug: "docs/deployment/scaling" },
            { label: "Secrets Management", slug: "docs/deployment/secrets-management" },
            { label: "Observability", slug: "docs/deployment/observability" },
          ],
        },
      ],
    }),
    tailwind({ applyBaseStyles: false }),
  ],
  // site: "https://llm-proxy.dev",
  site: "https://outward-vaned-vance.ngrok-free.dev/",
  vite: {
    server: {
      allowedHosts: true,
    },
  },
});
