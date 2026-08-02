from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def source(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


class KeychainMigrationTests(unittest.TestCase):
    def test_keychain_wrapper_supports_full_lifecycle(self) -> None:
        keychain = source("mini-ninebot/App/NinebotKeychain.swift")
        for symbol in (
            "SecItemCopyMatching",
            "SecItemAdd",
            "SecItemUpdate",
            "SecItemDelete",
            "kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly",
        ):
            self.assertIn(symbol, keychain)

    def test_legacy_credentials_are_migrated_before_shared_data_is_sanitized(self) -> None:
        keychain = source("mini-ninebot/App/NinebotKeychain.swift")
        view_model = source("mini-ninebot/App/NinebotViewModel.swift")
        shared_store = source("Shared/NinebotSharedStore.swift")

        self.assertIn("migrateLegacyCredentials", keychain)
        self.assertIn("migrateLegacyCredentials", view_model)
        self.assertIn("store.removeLegacyConfiguration()", view_model)
        self.assertIn('sharedConfiguration.bearerToken = ""', shared_store)
        self.assertIn("sharedConfiguration.appSessionToken = nil", shared_store)
        self.assertIn("sharedResult.sessionToken = nil", shared_store)

    def test_login_saves_session_and_logout_only_removes_session(self) -> None:
        view_model = source("mini-ninebot/App/NinebotViewModel.swift")
        self.assertIn(
            "try credentialStore.saveSessionToken(resolvedResult.sessionToken)",
            view_model,
        )
        logout = view_model.split("func logOut()", 1)[1].split("func selectVehicle", 1)[0]
        self.assertIn("try credentialStore.saveSessionToken(nil)", logout)
        self.assertNotIn("credentialStore.removeAll()", logout)
        self.assertNotIn('bearerToken = ""', logout)
        self.assertIn("store.saveConfiguration(currentConfiguration)", logout)

    def test_requests_keep_authorization_bearer_header(self) -> None:
        client = source("Shared/NinebotServerClient.swift")
        self.assertIn(
            'request.setValue("Bearer \\(token)", forHTTPHeaderField: "Authorization")',
            client,
        )

    def test_main_app_background_and_intents_resolve_keychain_credentials(self) -> None:
        for relative_path in (
            "mini-ninebot/App/NinebotViewModel.swift",
            "mini-ninebot/App/NinebotBackgroundTaskManager.swift",
            "mini-ninebot/App/NinebotPushManager.swift",
            "mini-ninebot/App/NinebotAppIntents.swift",
        ):
            self.assertIn("resolvedConfiguration", source(relative_path), relative_path)

    def test_widget_does_not_link_to_keychain_store(self) -> None:
        widget_root = PROJECT_ROOT / "NinebotWidgets"
        widget_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in widget_root.glob("*.swift")
        )
        self.assertNotIn("NinebotKeychain", widget_source)
        self.assertNotIn("NinebotCredentialStore", widget_source)


if __name__ == "__main__":
    unittest.main()
