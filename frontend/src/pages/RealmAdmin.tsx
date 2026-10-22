import { RealmPlayer } from "../website/realm";
import { PageHeader } from "../components/ui";
import "../website/website.css";

/** Admin → Realm (test): play Vortex Realm, the Warrior Kiwi game, without a member rank (the owner login opens it). */
export function RealmAdminPage() {
  return (
    <div className="page">
      <PageHeader title="Vortex Realm" subtitle="Test the Warrior Kiwi game. Members play it in their Passport → Realm tab from Warrior Kiwi up." />
      <div className="site vr-admin"><RealmPlayer /></div>
    </div>
  );
}
