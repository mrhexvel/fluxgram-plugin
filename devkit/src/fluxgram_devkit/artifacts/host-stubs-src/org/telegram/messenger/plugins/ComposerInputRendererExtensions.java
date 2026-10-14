package org.telegram.messenger.plugins;

import android.graphics.Canvas;

import org.telegram.messenger.plugins.api.ComposerExpandedBounds;
import org.telegram.messenger.plugins.api.ComposerGeometry;
import org.telegram.messenger.plugins.api.ComposerInput;
import org.telegram.messenger.plugins.api.ComposerSendEvent;
import org.telegram.messenger.plugins.api.ComposerTextClusterCapture;
import org.telegram.messenger.plugins.api.ComposerTextClusterPresentation;
import org.telegram.messenger.plugins.api.ComposerTextClusterSelection;
import org.telegram.messenger.plugins.api.ComposerTextLayoutChange;
import org.telegram.messenger.plugins.api.JavaPluginContext;
import org.telegram.messenger.plugins.api.JavaPluginUiResource;

public final class ComposerInputRendererExtensions {
    public static final String EXTENSION_ID = "ui.chat.composer.input.native_draw";
    public static final int CAPABILITY_INPUT = 1;
    public static final int CAPABILITY_TEXT_LAYOUT = 1 << 1;
    public static final int CAPABILITY_SEND = 1 << 2;
    public static final int CAPABILITY_EXPANDED_DRAW = 1 << 3;
    public static final int CAPABILITY_TEXT_CAPTURE = 1 << 4;

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
        Renderer create(Surface surface) throws Exception;
    }

    public interface Surface {
        int getAccount();

        long getOwnerId();

        long getDialogId();

        ComposerGeometry getGeometry();

        boolean isActive();

        void requestFrame();

        void selectTextClusters(ComposerTextClusterSelection selection);
    }

    public interface Renderer extends AutoCloseable {
        default int getCapabilities() {
            return 0;
        }

        default void onInput(ComposerInput input) throws Exception {
        }

        default void onTextLayoutChanged(ComposerTextLayoutChange change) throws Exception {
        }

        default void onSend(ComposerSendEvent event) throws Exception {
        }

        default boolean onFrame(long frameTimeNanos) throws Exception {
            return false;
        }

        Result draw(Frame frame) throws Exception;

        default void drawExpanded(ExpandedFrame frame) throws Exception {
        }

        @Override
        default void close() throws Exception {
        }
    }

    public interface Frame {
        Canvas getCanvas();

        ComposerGeometry getGeometry();

        int getWidth();

        int getHeight();

        ComposerTextClusterCapture getTextClusterCapture();

        ComposerTextClusterCapture getRemovedTextClusterCapture();

        void drawNative();

        void drawNative(ComposerTextClusterPresentation current,
                ComposerTextClusterPresentation removed);
    }

    public interface ExpandedFrame {
        Canvas getCanvas();

        ComposerGeometry getGeometry();

        ComposerExpandedBounds getBounds();
    }
}
