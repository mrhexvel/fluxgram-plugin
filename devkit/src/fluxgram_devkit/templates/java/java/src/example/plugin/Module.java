package example.plugin;

import android.graphics.Canvas;

import org.telegram.messenger.plugins.ComposerInputRendererExtensions;
import org.telegram.messenger.plugins.api.JavaPluginContext;
import org.telegram.messenger.plugins.api.JavaPluginModule;
import org.telegram.messenger.plugins.api.JavaPluginUiResource;

public final class Module implements JavaPluginModule {
    private JavaPluginUiResource resource;

    @Override
    public void onLoad(JavaPluginContext context) {
        resource = ComposerInputRendererExtensions.register(context, Renderer::new);
        context.log("Composer Renderer loaded");
    }

    @Override
    public void onUnload() {
        if (resource != null) {
            resource.close();
            resource = null;
        }
    }

    private static final class Renderer implements ComposerInputRendererExtensions.Renderer {
        @Override
        public ComposerInputRendererExtensions.Result draw(
                ComposerInputRendererExtensions.Frame frame) {
            Canvas canvas = frame.getCanvas();
            int save = canvas.save();

            try {
                canvas.scale(0.98f, 0.98f, frame.getWidth() / 2.0f, frame.getHeight() / 2.0f);
                frame.drawNative();
                return ComposerInputRendererExtensions.Result.COMMIT;
            } finally {
                canvas.restoreToCount(save);
            }
        }
    }
}
