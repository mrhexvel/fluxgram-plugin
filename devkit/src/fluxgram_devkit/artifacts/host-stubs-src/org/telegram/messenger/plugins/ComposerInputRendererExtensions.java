package org.telegram.messenger.plugins;

import android.graphics.Canvas;
import android.view.View;

import org.telegram.messenger.plugins.api.JavaPluginContext;
import org.telegram.messenger.plugins.api.JavaPluginUiResource;

public final class ComposerInputRendererExtensions {
    public static final String EXTENSION_ID = "ui.chat.composer.input.native_draw";

    private ComposerInputRendererExtensions() {
    }

    public static JavaPluginUiResource register(JavaPluginContext context, Factory factory) {
        throw new UnsupportedOperationException();
    }

    public enum Result {
        COMMIT,
        FALLBACK
    }

    public interface Factory {
        Renderer create() throws Exception;
    }

    public interface Renderer extends AutoCloseable {
        Result draw(Frame frame) throws Exception;

        @Override
        default void close() throws Exception {
        }
    }

    public interface Frame {
        View getView();

        Canvas getCanvas();

        int getWidth();

        int getHeight();

        void drawNative();
    }
}
