package io.github.miffk.bypasshub.ui

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.StateListDrawable
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Switch
import android.widget.TextView

/** Оформление в духе BypassHub для Windows: тёмная тема и градиент акцентных цветов.
 *  Всё кликабельное получает рамку при фокусе — так приложением удобно управлять пультом на ТВ. */
object Ui {
    val BG = Color.parseColor("#101418")
    val SURFACE = Color.parseColor("#1b2129")
    val SURFACE2 = Color.parseColor("#262d37")
    val BORDER = Color.parseColor("#36404c")
    val TEXT = Color.parseColor("#e8eaed")
    val MUTED = Color.parseColor("#9aa3ad")
    val ACCENT = Color.parseColor("#7f5af0")
    val ACCENT2 = Color.parseColor("#2cb67d")
    val GREEN = Color.parseColor("#3ddc84")
    val YELLOW = Color.parseColor("#f6c343")
    val RED = Color.parseColor("#ff6b6b")
    val FOCUS = Color.parseColor("#c9b8ff")
    val SOFT_RED = Color.parseColor("#ff8f80")
    val NAV = Color.parseColor("#0b0f13")                 // нижняя панель
    val GLASS = Color.argb(0xCC, 0x1b, 0x21, 0x29)          // полупрозрачные карточки главной
    val DIVIDER = Color.argb(0x40, 0x9a, 0xa3, 0xad)
    val TRACK = Color.parseColor("#0a0d11")               // дорожка переключателя
    val THUMB = Color.parseColor("#3a4658")
    val THUMB_CIRCLE = Color.parseColor("#55657c")

    fun Context.dp(v: Float): Int = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, resources.displayMetrics).toInt()
    fun Context.dp(v: Int): Int = dp(v.toFloat())

    fun rounded(context: Context, color: Int, radiusDp: Int, stroke: Int = 0, strokeDp: Int = 0): GradientDrawable =
        GradientDrawable().apply {
            setColor(color)
            cornerRadius = context.dp(radiusDp).toFloat()
            if (strokeDp > 0) setStroke(context.dp(strokeDp), stroke)
        }

    fun gradient(context: Context, radiusDp: Int, colors: IntArray = intArrayOf(ACCENT, ACCENT2),
                 shape: Int = GradientDrawable.RECTANGLE): GradientDrawable =
        GradientDrawable(GradientDrawable.Orientation.TL_BR, colors).apply {
            this.shape = shape
            if (shape == GradientDrawable.RECTANGLE) cornerRadius = context.dp(radiusDp).toFloat()
        }

    /** Фон с состояниями: обычный, нажатый, в фокусе (рамка — для пульта ТВ). */
    fun stateful(context: Context, normal: Drawable, radiusDp: Int, pressedColor: Int = SURFACE2): Drawable =
        StateListDrawable().apply {
            val focused = rounded(context, Color.TRANSPARENT, radiusDp, FOCUS, 3)
            addState(intArrayOf(android.R.attr.state_focused),
                android.graphics.drawable.LayerDrawable(arrayOf(normal.constantState!!.newDrawable().mutate(), focused)))
            addState(intArrayOf(android.R.attr.state_pressed), rounded(context, pressedColor, radiusDp))
            addState(intArrayOf(), normal)
        }

    fun text(context: Context, value: CharSequence, sizeSp: Float = 15f, color: Int = TEXT, bold: Boolean = false): TextView =
        TextView(context).apply {
            text = value
            setTextColor(color)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, sizeSp)
            if (bold) typeface = Typeface.DEFAULT_BOLD
        }

    fun button(context: Context, label: String, primary: Boolean = true, onClick: () -> Unit): TextView =
        TextView(context).apply {
            text = label
            gravity = Gravity.CENTER
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(if (primary) Color.WHITE else TEXT)
            val pad = context.dp(12)
            setPadding(context.dp(18), pad, context.dp(18), pad)
            background = stateful(context,
                if (primary) gradient(context, 12) else rounded(context, Color.TRANSPARENT, 12, BORDER, 1), 12)
            isFocusable = true
            isClickable = true
            setOnClickListener { onClick() }
        }

    fun card(context: Context): LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        background = rounded(context, SURFACE, 18)
        val p = context.dp(18)
        setPadding(p, p, p, p)
    }

    fun vbox(context: Context): LinearLayout = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL }

    fun hbox(context: Context): LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
    }

    fun lp(w: Int = ViewGroup.LayoutParams.MATCH_PARENT, h: Int = ViewGroup.LayoutParams.WRAP_CONTENT,
           top: Int = 0, bottom: Int = 0, weight: Float = 0f): LinearLayout.LayoutParams =
        LinearLayout.LayoutParams(w, h, weight).apply {
            topMargin = top
            bottomMargin = bottom
        }

    fun View.margins(top: Int = 0, bottom: Int = 0, start: Int = 0, end: Int = 0): View {
        val p = (layoutParams as? ViewGroup.MarginLayoutParams)
            ?: LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        p.setMargins(context.dp(start), context.dp(top), context.dp(end), context.dp(bottom))
        layoutParams = p
        return this
    }

    fun switch(context: Context, label: String, checked: Boolean, onChange: (Boolean) -> Unit): Switch =
        Switch(context).apply {
            text = label
            isChecked = checked
            setTextColor(TEXT)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            val states = arrayOf(intArrayOf(android.R.attr.state_checked), intArrayOf())
            thumbTintList = ColorStateList(states, intArrayOf(Color.WHITE, Color.parseColor("#c7ccd1")))
            trackTintList = ColorStateList(states, intArrayOf(ACCENT2, BORDER))
            background = stateful(context, rounded(context, Color.TRANSPARENT, 10), 10)
            setPadding(context.dp(6), context.dp(10), context.dp(6), context.dp(10))
            setOnCheckedChangeListener { _, v -> onChange(v) }
        }

    fun input(context: Context, hint: String, multiline: Boolean = false): EditText = EditText(context).apply {
        this.hint = hint
        setTextColor(TEXT)
        setHintTextColor(MUTED)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
        background = stateful(context, rounded(context, SURFACE2, 12), 12)
        val p = context.dp(12)
        setPadding(p, p, p, p)
        if (multiline) {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE or
                InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            minLines = 3
            maxLines = 6
            gravity = Gravity.TOP or Gravity.START
            typeface = Typeface.MONOSPACE
        } else {
            inputType = InputType.TYPE_CLASS_TEXT
            isSingleLine = true
        }
    }

    /** (текст, цвет) задержки. */
    fun pingText(ms: Long?, measured: Boolean): Pair<String, Int> = when {
        !measured -> "—" to MUTED
        ms == null -> "нет ответа" to RED
        ms < 150 -> "$ms мс" to GREEN
        ms < 350 -> "$ms мс" to YELLOW
        else -> "$ms мс" to RED
    }
}
