package example.plugin;

import org.telegram.messenger.plugins.api.JavaPluginContext;
import org.telegram.messenger.plugins.api.JavaPluginModule;

public final class Module implements JavaPluginModule {
    private JavaPluginContext context;

    @Override
    public void onLoad(JavaPluginContext context) {
        this.context = context;
        context.log("Hello Plugin Java module loaded");
    }

    @Override
    public void onSettingChanged(String key, Object value) {
        if (context != null && "enabled".equals(key)) {
            context.log("enabled=" + value);
        }
    }

    @Override
    public void onUnload() {
        context = null;
    }
}
