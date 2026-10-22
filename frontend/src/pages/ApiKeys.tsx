import { ApiKeysCard, EnvKeyCard } from "../components/ApiKeysCard";
import { PageHeader } from "../components/ui";

export function ApiKeysPage() {
  return (
    <>
      <PageHeader title="API keys" subtitle="Keys for the Pokémon market data sources" />
      <EnvKeyCard />
      <ApiKeysCard />
    </>
  );
}
